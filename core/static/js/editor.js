import { formatSecondsToString, createElementFromHTMLString, getCSRFToken, animateDuringPlayback } from "./utils.js";

// Clicking or dragging inside these regions seeks the video: the ticks/scrubber
// row and every track row's right-hand (scrollable) area. Clicks on a
// `.track-item-content` are excluded so they can still select the annotation.
const SEEK_REGION_SELECTOR = '#timeline-row-ticks-and-scrubbers, .timeline-track-row-right';

function convertPercentStringToDecimal(percentString) {
  if (typeof(percentString) === 'string') {
    const newString = percentString.replace('%', '');
    return parseFloat(newString) / 100;
  }

  return;
}

export class Editor {
    constructor() {
        this.video = document.querySelector('.annotation-player-container video');
        this.duration = this.video.duration;
        this.annotationBox = window.videoPlayer.annotationBox;
        this.dragState = null;
        this.contentId = null;
        this.listenForNewItemCreation();
        this.typeOfAnnotationInFocus = null;
        this.annotationIdInFocus = null;
        this.activeCensorPosition = null;
        this.tickMarksContainer = document.querySelector('#tick-marks-container');
        this.timelineTicksContent = document.getElementById('timeline-ticks-content');
        this.timelineWrapper = document.getElementById('timeline-wrapper');
        this.zoomSliderInput = document.getElementById('timeline-scroll-input');
        this.editorScrubber = document.querySelector('#editor-scrubber');
        this.zoomLevel = 1;
        this.scrubberBounds = null;
        this.timelineScrubber = null;
        this.isDragging = false;
        this.wasPlayingBeforeDrag = false;
        this.annotationUpdatedEvent = new CustomEvent("annotationUpdated");
        this.selectedSubtitleTrackId = null;
        this.itemBeingDragged = null;
        this.dragTimeStart = null;

        this.init();
    }

    init() {
        const playerContainer = document.getElementById("annotation-player-container");
        this.contentId = playerContainer.dataset["contentid"];
        // Event delegation for drag/resize - selection is handled by HTMX attributes
        this.updateTracks();
        document.addEventListener('mousemove', this.handleMouseMove.bind(this));
        document.addEventListener('mouseup', this.handleMouseUp.bind(this));

        // Listen for "set time" button clicks
        document.addEventListener('click', (e) => {
            if (e.target.dataset.setTime) {
                this.setTimeFromVideo(e.target.dataset.setTime);
            }
        });
        this.placeTrackItems();
        document.body.addEventListener('htmx:afterSettle', this.handleTrackItemPlacementAfterEvent.bind(this));

        this.renderTickMarksAndLabels();
        this.attachZoomListener();
        this.createtimelineScrubber();
        this.attachTimelineListeners();
        this.attachVideoListeners();
        this.setupTracks();
        this.watchForTrackCreation();
        this.watchForClickOutsideOfTrackMenu();
        this.watchForTimelineScrollChangeAndHandleIt();
        this.watchAndHandleAnnotationSetMenuOpen();
        this.watchForAnnotationSetNameChangeAndHandleIt();
        this.watchAndHandleAnnotationSetDelete();
        this.setupAnnotationSetOptionsModal();
        this.watchAndHandleAnnotationSetExport();
        this.attachRemoveEditorListeners();
        this.watchForEditorSearchInputAndHandleIt();
        this.watchAndHandleEditorPanelSwitch();
        this.watchAndHandleSubtitleTrackChange();
        this.handleNoAnnotationSet();
    }

    updateTracks() {
      const tracks = document.querySelectorAll('.track-row-annotations-container');
      tracks.forEach(container => {
          container.addEventListener('mousedown', this.handleMouseDown.bind(this));
      });
      this.tracks = tracks;
    }

    startResize(trackItem, handle, e) {
        const isLeft = handle.classList.contains('resize-handle-left');
        const itemContainer = trackItem.closest('.track-row-annotations-container');
        const rect = itemContainer.getBoundingClientRect();
        const containerWidth = itemContainer.scrollWidth || rect.width;
        const itemLeft = this.calculateItemLeftAsDecimal(trackItem);
        const itemWidth = this.calculateItemWidthAsDecimal(trackItem);

        this.dragState = {
            type: 'resize',
            item: trackItem,
            container: itemContainer,
            handle,
            isLeft,
            startX: e.clientX,
            startLeft: itemLeft * 100,
            startWidth: itemWidth * 100,
            containerWidth,
            hasMoved: false,
            originalLeft: itemLeft,
            originalWidth: itemWidth
        };

        // Reset deltas
        trackItem.dataset.deltaLeft = '0';
        trackItem.dataset.deltaWidth = '0';

        document.body.classList.add('resizing', 'resizing-item');

        // Seek video to the handle position being dragged
        this.seekToHandlePosition(isLeft, parseFloat(trackItem.style.left), parseFloat(trackItem.style.width));
    }

    handleMouseDown(e) {
        const trackItem = e.target.closest('.track-item');
        if (!trackItem) return;

        const resizeHandle = e.target.closest('.resize-handle');

        if (resizeHandle) {
            this.startResize(trackItem, resizeHandle, e);
            e.preventDefault();
            e.stopPropagation();
        }
    }

    calculateItemLeftAsDecimal(item) {
      const startTime = parseFloat(item.dataset["start"]);
      return startTime / this.duration;

    }

    calculateItemWidthAsDecimal(item) {
      const startTime = parseFloat(item.dataset["start"]);
      const endTime = parseFloat(item.dataset["end"]);
      return (endTime - startTime) / this.duration;
    }

    handleMouseMove(e) {
        if (!this.dragState) return;

        // Define a threshold (in pixels) to determine if this is a real drag
        const DRAG_THRESHOLD = 3;

        const deltaX = Math.abs(e.clientX - this.dragState.startX);

        // Only mark as moved if we've exceeded the threshold
        if (deltaX > DRAG_THRESHOLD) {
            this.dragState.hasMoved = true;
        }

        // Only update position if we've started moving
        if (this.dragState.hasMoved) {
            if (this.dragState.type === 'resize') {
                this.updateResizePosition(e);
            }
        }

        e.preventDefault();
    }

    hideOrShowResizeHandles(itemElement) {
      // we have to explicitly check for "track-item" class because other class names
      // contain the string "track-item". Otherwise we could have just checked for the
      // "track-item" substring in element.className
      let isTrackItem = false;
      for (let cl of itemElement.classList) {
        if (cl == "track-item") isTrackItem = true;
        break;
      }

      if (!isTrackItem) return;

      const hiddenClass = "resize-handle-hidden";
      const resizeHandles = itemElement.querySelectorAll(".resize-handle");
      // getBoundingClientRect provides size values in px.
      const triggerWidth = 60;
      const shouldHideHandles = itemElement.getBoundingClientRect().width < triggerWidth;
      for (let handle of resizeHandles) {
        if (shouldHideHandles) {
          handle.classList.add(hiddenClass);
        }
        else {
          handle.classList.remove(hiddenClass);
        }
      }
    }

    updateResizePosition(e) {
        const deltaX = e.clientX - this.dragState.startX;
        // Don't account for zoom in percent calculation - container width is already adjusted
        const deltaPercent = (deltaX / this.dragState.containerWidth) * 100;

        if (this.dragState.isLeft) {
            let newLeft = this.dragState.startLeft + deltaPercent;
            let newWidth = this.dragState.startWidth - deltaPercent;

            newLeft = Math.max(0, newLeft);
            newWidth = Math.max(1, newWidth);

            if (newLeft + newWidth > 100) {
                newWidth = 100 - newLeft;
            }

            this.dragState.item.style.left = `${newLeft}%`;
            this.dragState.item.style.width = `${newWidth}%`;

            const deltaLeft = newLeft - this.dragState.originalLeft;
            const deltaWidth = newWidth - this.dragState.originalWidth;
            this.dragState.item.dataset.deltaLeft = deltaLeft.toFixed(2);
            this.dragState.item.dataset.deltaWidth = deltaWidth.toFixed(2);

            this.seekToHandlePosition(true, newLeft, newWidth);
        } else {
            let newWidth = this.dragState.startWidth + deltaPercent;

            newWidth = Math.max(1, newWidth);
            const maxWidth = 100 - this.dragState.startLeft;
            newWidth = Math.min(newWidth, maxWidth);

            this.dragState.item.style.width = `${newWidth}%`;

            const deltaWidth = newWidth - this.dragState.originalWidth;
            this.dragState.item.dataset.deltaWidth = deltaWidth.toFixed(2);
            this.hideOrShowResizeHandles(this.dragState.item);

            this.seekToHandlePosition(false, this.dragState.startLeft, newWidth);
        }
    }

    seekToHandlePosition(isLeft, leftPercent, widthPercent) {
        const video = document.querySelector('.annotation-player-container video');
        if (!video) return;

        // Calculate time based on which handle is being dragged
        const timePercent = isLeft ? leftPercent : (leftPercent + widthPercent);
        const targetTime = (timePercent / 100) * this.duration;

        // Seek the video (this will trigger timeupdate and update the scrubber)
        video.currentTime = targetTime;

        // Also update via the player API if available
        if (window.videoPlayer && window.videoPlayer.skipTo) {
            window.videoPlayer.skipTo(targetTime);
        }
    }

    handleMouseUp(e) {
        if (!this.dragState) return;

        const state = this.dragState;
        this.dragState = null;

        document.body.classList.remove('resizing', 'resizing-item');

        if (state.hasMoved) {
            this.triggerSave(state);
            // Prevent the click event from firing if we actually dragged
            e.preventDefault();
            e.stopPropagation();

            // Also prevent the next click event (for better browser compatibility)
            const preventNextClick = (clickEvent) => {
                clickEvent.preventDefault();
                clickEvent.stopPropagation();
                document.removeEventListener('click', preventNextClick, true);
            };
            document.addEventListener('click', preventNextClick, true);
        }
        // If !hasMoved, don't prevent - let the click bubble to HTMX
    }

    setUpItemElevationOnMousedown(item) {
      item.addEventListener("mousedown", () => {
        const parent = item.closest(".track-row-annotations-container");
        for (let sibling of parent.querySelectorAll(".track-item")) {
          if (sibling != item) {
            sibling.classList.remove("elevated");
          } else {
            sibling.classList.add("elevated");
          }
        }
      });
    }

    setUpItemClickListeners(element) {
      const annotationType = element.dataset["annotationType"];
      const annotationId = element.dataset["annotationId"];
      element.addEventListener("click", async (e) => {
        e.preventDefault();
        this.getItemFormDetails(annotationType, annotationId, this.contentId);
        this.markItemAsActive(annotationType, annotationId);
      });

      // set up censor position locator listeners
      if (annotationType == "censor") {
        const positionLocators = element.querySelectorAll(".censor-position-locator");
        for (let positionLocator of positionLocators) {
          positionLocator.addEventListener("click", (e) => {
            // allow propagation only if the parent item is not active
            const parentItem = element.closest(".track-item");
            if (parentItem.className.includes("active-track-item")) {
              e.stopPropagation();
              this.video.currentTime = parseFloat(positionLocator.dataset["positionTime"]);
              this.markCensorPositionAsActive(positionLocator.dataset["positionId"]);
              return;
            }
            this.video.currentTime = parseFloat(positionLocator.dataset["positionTime"]);
            this.markCensorPositionAsActive(positionLocator.dataset["positionId"]);
          })
        }
      }
    }

    blockTrackItemPointerEvents() {
      const containers = document.getElementsByClassName("track-row-annotations-container");
      for (let container of containers) {
        container.classList.add("annotations-container-no-child-pointer-events");
      }
    }

    allowTrackItemPointerEvents() {
      const containers = document.getElementsByClassName("track-row-annotations-container");
      for (let container of containers) {
        container.classList.remove("annotations-container-no-child-pointer-events");
      }
    }

    resetTrackItemProjectionsStyle() {
      const projections = this.timelineWrapper.querySelectorAll(".track-item-projection");
      for (let projection of projections) {
        projection.style.width = "";
        projection.style.left = "";
        projection.style.top = "5px";
      }
    }

    setupItemDragListeners(item) {
      // setup the data stored in the drag event
      item.addEventListener("dragstart", (event) => {
        this.itemBeingDragged = item;
        this.dragTimeStart = Date.now();
        this.resetTrackItemProjectionsStyle();
        this.blockTrackItemPointerEvents();
        event.dataTransfer.setData("text/html", item.outerHTML);
        event.dataTransfer.setData("text/plain", item.id);
        const img = new Image()
        img.src = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
        event.dataTransfer.setDragImage(img, 0, 0);
        item.classList.add("is-dragging");
      });

      item.addEventListener("dragend", () => {
        this.itemBeingDragged = null;
        this.dragTimeStart = null;
        item.classList.remove("is-dragging");
        this.allowTrackItemPointerEvents();
      })
    }

    setupItems() {
      const itemsToSetUp = document.querySelectorAll(".track-item[data-setup='false']");
      for (let item of itemsToSetUp) {
        this.setUpItemElevationOnMousedown(item);
        this.setUpItemClickListeners(item);
        this.setupItemDragListeners(item);
        item.dataset["setup"] = "true";
      }
    }

    listenForItemUpdateFormSubmission() {
      const itemForm = document.getElementById("annotation-update-form");
      if (!itemForm) {
        return;
      }
      const annotationId = itemForm.dataset["annotationId"];
      const annotationType = itemForm.dataset["annotationType"];
      itemForm.addEventListener("submit", (e) => {
        e.preventDefault();
        // we don't want to submit the form when a censor position is deleted.
        // That change is handled in a different way.
        if (e.submitter.classList.contains("censor-position-delete-button")) {
          return;
        }
        this.updateAnnotation({annotationType, annotationId})
      })
    }

    async deleteItem(annotationType, annotationId) {
      const response = await fetch(`/annotations/${annotationType}/${annotationId}/delete`, {
        method: "delete",
        headers: {"X-CSRFToken": getCSRFToken()}
      });

      if (!response.ok) {
        console.error("Failed to delete item!");
        return;
      }

      if(this.typeOfAnnotationInFocus == "censor") {
        this.handleFocusChangeAwayFromCensorType();
      }
      window.dispatchEvent(this.annotationUpdatedEvent);

      // remove item from panel
      const panelItemToRemove = document.getElementById(`${annotationType}-panel-item-${annotationId}`);
      if (panelItemToRemove) {
        panelItemToRemove.remove();
      } else {
        console.error("Failed to remove deleted panel item");
      }

      // remove item from track
      const trackItemToRemove = this.timelineWrapper.querySelector(`.track-item[data-annotation-type="${annotationType}"][data-annotation-id="${annotationId}"]`);
      if (trackItemToRemove) {
        trackItemToRemove.remove();
      } else {
        console.error("Failed to remove deleted track item");
      }

      // check if right side panel form should be emptied
      const itemForm = document.getElementById("existing-item-form");
      if (itemForm) {
        const itemFormType = itemForm.dataset["annotationType"];
        const itemFormId = itemForm.dataset["annotationId"];
        if (annotationType == itemFormType && annotationId == itemFormId) {
          const detailForm = document.getElementById("detail-form");
          detailForm.innerHTML = "";
        }
      }
      this.placeTrackItems();
    }

    async setUpItemFormDeleteButton() {
      const itemForm = document.getElementById("existing-item-form");
      if (!itemForm) {
        return;
      }
      const deleteItemButton = itemForm.querySelector("#annotation-form-delete-button");
      if (!deleteItemButton) {
        return;
      }

      const annotationType = itemForm.dataset["annotationType"];
      const annotationId = itemForm.dataset["annotationId"];
      deleteItemButton.addEventListener("click", async (e) => {
        e.preventDefault();
        await this.deleteItem(annotationType, annotationId);
      });
    }

    setUpItemForm() {
      this.listenForItemUpdateFormSubmission();
      this.setUpItemFormDeleteButton();
      this.changeAnnotationInFocus();
    }

    getCensorPositions() {
      if (this.typeOfAnnotationInFocus != "censor") {
        return;
      }
      const positionsWrapper = document.getElementById("censor-positions-wrapper");
      const positionEls = positionsWrapper.querySelectorAll(".position-entry");
      const positions = [];
      for (let positionEl of positionEls) {
        const timeInput = positionEl.querySelector(".position-time-input");
        let time = 0.0;
        if (timeInput) {
          time = parseFloat(timeInput.value).toFixed(2);
        }
        positions.push({
          "id": positionEl.dataset["positionId"],
          "time": time
        });
      }
      return positions;
    }

    placeNewCensorPositionHtml(censor_parent_id, itemAndPositionsHtml) {
      const annotationUpdateForm = document.getElementById("existing-item-form");
      const currentFormId = annotationUpdateForm.dataset["annotationId"];
      const censorPositionWrapperEl = document.getElementById("censor-positions-wrapper");
      // don't do anything if the user has moved onto a different item
      if (!censorPositionWrapperEl || censor_parent_id != currentFormId) {
        return;
      }
      censorPositionWrapperEl.outerHTML = itemAndPositionsHtml["censorPositions"];

      const trackItemToUpdate = this.timelineWrapper.querySelector(`.track-item[data-annotation-id='${censor_parent_id}']`);
      trackItemToUpdate.outerHTML = itemAndPositionsHtml["trackItem"];
      this.placeTrackItems();
      this.setUpCensorPositionDeleteListeners(censor_parent_id)
      this.setupCensorPositionSeekListeners();
      return;
    }

    async createCensorPosition(parentCensorId, time, x, y, width, height, parentStartTime, parentEndTime) {
      if (parseFloat(parentStartTime) > parseFloat(time) || parseFloat(parentEndTime) < parseFloat(time)) {
        return;
      }
      const response = await fetch("/annotations/censor-position/create", {
        method: "POST",
        headers: {"X-CSRFToken": getCSRFToken(), "Content-Type": "application/json"},
        body: JSON.stringify({parent_annotation_id: parentCensorId, time, x, y, width, height})
      });
      if (response.ok) {
        const responseHtmlMap = await response.json();
        this.placeNewCensorPositionHtml(parentCensorId, responseHtmlMap)
        window.dispatchEvent(this.annotationUpdatedEvent);
      }
      else if (!response.ok) {
        console.error("Failed to create censor position");
      }
    }

    async updateCensorPosition(positionId, time, x, y, width, height, parentAnnotationId) {
      const response = await fetch("/annotations/censor-position/update", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({position_id: positionId, time, x, y, width, height})
      });
      if (response.ok) {
        const responseHtmlMap = await response.json();
        this.placeNewCensorPositionHtml(parentAnnotationId, responseHtmlMap)
        window.dispatchEvent(this.annotationUpdatedEvent);
      }
      else {
        console.error("Failed to update censor position");
      }
    }

    async deleteCensorPosition(parentAnnotationId, positionId) {
      const response = await fetch(`/annotations/censor-position/delete/${positionId}`, {
        method: "DELETE",
        headers: {"X-CSRFToken": getCSRFToken()}
      });
      if (response.status == 200) {
        const censorPositionLocatorToDelete = document.querySelector(`.censor-position-locator[data-position-id='${positionId}']`);
        const censorPositionEntryToDelete = document.querySelector(`.position-entry[data-position-id='${positionId}']`);
        censorPositionLocatorToDelete.remove();
        censorPositionEntryToDelete.remove();
      }
      else if (!response.ok) {
        console.error("Failed to delete censor position");
      }
    }

    async handleCensorAnnotationBoxClick(e) {
      if (e.target.className.includes("censor-position")) {
        return;
      }
      const annotationBoxDim = e.target.getBoundingClientRect();
      let x = e.layerX / annotationBoxDim.width * 100;
      let y = e.layerY / annotationBoxDim.height * 100;
      const width = Math.min(100 - x, 12);
      x = x - width / 2;
      const height = Math.min(100 - y, 9);
      y = y - height / 2;
      const time = parseFloat(this.video.currentTime).toFixed(2);

      const itemForm = document.getElementById("existing-item-form");
      const annotationId = itemForm.dataset["annotationId"];

      const currentPositions = this.getCensorPositions();
      const existingPosition = currentPositions.find(position => Math.abs(position.time - time) < 0.01);

      if (existingPosition?.id) {
        await this.updateCensorPosition(existingPosition.id, time, x, y, width, height, annotationId)
      }
      else {
        const startTimeEl = document.getElementById("start_time");
        const parentStartTime = parseFloat(startTimeEl.value).toFixed(2);
        const endTimeEl = document.getElementById("end_time");
        const parentEndTime = parseFloat(endTimeEl.value).toFixed(2);
        await this.createCensorPosition(annotationId, time, x, y, width, height, parentStartTime, parentEndTime)
      }
    }

    buildMoveHandler(elementToMove) {
      let lastEventClientX;
      let lastEventClientY;
      return (event) => {
        if (lastEventClientX !== undefined && lastEventClientY !== undefined) {
          // look at difference in last event's position vs this events position
          const xChange = event.clientX - lastEventClientX;
          const yChange = event.clientY - lastEventClientY;
          const referenceRect = elementToMove.parentElement.getBoundingClientRect();
          const xPercentChange = xChange / referenceRect.width * 100;
          const yPercentChange = yChange / referenceRect.height * 100;

          const elementLeft = parseFloat(elementToMove.style.left);
          const elementTop = parseFloat(elementToMove.style.top);

          const newLeft = (elementLeft + xPercentChange) + '%';
          const newTop = (elementTop + yPercentChange) + '%';

          elementToMove.style.left = newLeft;
          elementToMove.style.top = newTop;
        }
        lastEventClientX = event.clientX;
        lastEventClientY = event.clientY;
      }
    }

    buildResizePointMoveHandler(minHeightPercent = 4, minWidthPercent = 3) {
      return (event) => {
        event.stopPropagation();
        const annotationBox = event.target.closest(".annotation-box");
        const boxRect = annotationBox.getBoundingClientRect();
        const parentEl = event.target.parentElement;
        const newX = (event.clientX - boxRect.left) / boxRect.width * 100;
        const newY = (event.clientY - boxRect.top) / boxRect.height * 100;
        const curLeft = parseFloat(parentEl.style.left);
        const curTop = parseFloat(parentEl.style.top);
        const curWidth = parseFloat(parentEl.style.width);
        const curHeight = parseFloat(parentEl.style.height);
        const fixedRight = curLeft + curWidth;
        const fixedBottom = curTop + curHeight;
        const movesLeft = event.target.classList.contains("resize-point-left");
        const movesTop = event.target.classList.contains("resize-point-top");

        if (movesLeft) {
          const newLeft = Math.max(0, Math.min(newX, fixedRight - minWidthPercent));
          parentEl.style.left = `${newLeft}%`;
          parentEl.style.width = `${fixedRight - newLeft}%`;
        } else {
          parentEl.style.width = `${Math.max(minWidthPercent, Math.min(newX - curLeft, 100 - curLeft))}%`;
        }

        if (movesTop) {
          const newTop = Math.max(0, Math.min(newY, fixedBottom - minHeightPercent));
          parentEl.style.top = `${newTop}%`;
          parentEl.style.height = `${fixedBottom - newTop}%`;
        } else {
          parentEl.style.height = `${Math.max(minHeightPercent, Math.min(newY - curTop, 100 - curTop))}%`;
        }
      }
    }

    handleCensorPointerDown(e) {
      e.preventDefault();
      e.stopPropagation();
      const censorEl = e.currentTarget;
      const censorLeftStart = censorEl.style.left;
      const censorTopStart = censorEl.style.top;
      const censorPointerId = e.pointerId;
      censorEl.setPointerCapture(censorPointerId);
      const annotationBox = censorEl.closest("#annotation-box");
      const boxRect = annotationBox.getBoundingClientRect();
      const widthPercent = parseFloat(censorEl.style.width);
      const heightPercent = parseFloat(censorEl.style.height);

      async function onPointerUp(upEvent) {
        const positionEl = upEvent.target;
        const positionRect = positionEl.getBoundingClientRect();
        const censorPositionId = positionEl.dataset["censorPositionId"];
        const parentCensorId = positionEl.dataset["censorPositionParentId"];
        const newX = ((positionRect.left - boxRect.left) / boxRect.width) * 100;
        const newY = ((positionRect.top - boxRect.top) / boxRect.height) * 100;
        await this.updateCensorPosition(censorPositionId, this.video.currentTime, newX, newY, widthPercent, heightPercent, parentCensorId);
        handleCleanup();
      }

      function handleMoveCancel() {
        handleCleanup();
        censorEl.style.left = censorLeftStart;
        censorEl.style.top = censorTopStart;
      }

      function handleEscKeyPress(keyupEvent) {
        if (keyupEvent.defaultPrevented) {
          return;
        }

        if (keyupEvent.key == "Escape") {
          handleMoveCancel();
        }
      }

      const pointerUpCallback = onPointerUp.bind(this);

      const handleCensorMove = this.buildMoveHandler(censorEl);
      function handleCleanup() {
        censorEl.releasePointerCapture(censorPointerId);
        censorEl.removeEventListener('pointermove', handleCensorMove);
        censorEl.removeEventListener('pointerup', pointerUpCallback);
        censorEl.removeEventListener('pointercancel', handleMoveCancel);
        document.removeEventListener("keyup", handleEscKeyPress);
      }

      document.addEventListener("keyup", handleEscKeyPress);
      censorEl.addEventListener('pointercancel', handleMoveCancel);
      censorEl.addEventListener('pointerup', pointerUpCallback);
      censorEl.addEventListener('pointermove', handleCensorMove);
    }


    handleFocusChangeToCensorType() {
      this.annotationBox.className = "annotation-box annotation-box-censor-editor";
      this.annotationBoxCensorListener = this.handleCensorAnnotationBoxClick.bind(this);
      this.annotationBox.addEventListener("click", this.annotationBoxCensorListener);
      const censorPositions = document.getElementsByClassName("censor-position");
      for (let position of censorPositions) {
        if (position.dataset["censorPositionParentId"] == this.annotationIdInFocus) {
          this.activeCensorPosition = position;
          this.activeCensorPosition.classList.add("active-censor-position");
          break;
        } else {
          position.classList.remove("active-censor-position");
        }
      }

      // build resize points on corners
      if (this.activeCensorPosition) {
        const cornerData = ["resize-point-top resize-point-left", "resize-point-top", "resize-point-left", ""];

        for (const cssClass of cornerData) {
          const point = document.createElement("div");
          point.className = `censor-position-adjustment-point ${cssClass} resize-point`;

          point.addEventListener("pointerdown", (ptrDownEvent) => {
            ptrDownEvent.stopPropagation();
            ptrDownEvent.preventDefault();
            point.setPointerCapture(ptrDownEvent.pointerId);

            const startLeft = this.activeCensorPosition.style.left;
            const startTop = this.activeCensorPosition.style.top;
            const startWidth = this.activeCensorPosition.style.width;
            const startHeight = this.activeCensorPosition.style.height;
            let resizeCancelled = false;

            const onMove = this.buildResizePointMoveHandler();

            function handleCleanup() {
              point.releasePointerCapture(ptrDownEvent.pointerId);
              point.removeEventListener("pointermove", onMove);
              point.removeEventListener("pointerup", onPointerUp);
              point.removeEventListener("pointercancel", onCancel);
              document.removeEventListener("keyup", handleEscKeyPress);
            }

            async function onPointerUp() {
              handleCleanup();
              if (resizeCancelled) return;

              const newLeft = parseFloat(this.activeCensorPosition.style.left);
              const newTop = parseFloat(this.activeCensorPosition.style.top);
              const newWidth = parseFloat(this.activeCensorPosition.style.width);
              const newHeight = parseFloat(this.activeCensorPosition.style.height);
              const positionId = this.activeCensorPosition.dataset["censorPositionId"];
              const parentId = this.activeCensorPosition.dataset["censorPositionParentId"];
              await this.updateCensorPosition(positionId, this.video.currentTime, newLeft, newTop, newWidth, newHeight, parentId);
            }

            function onCancel() {
              this.activeCensorPosition.style.left = startLeft;
              this.activeCensorPosition.style.top = startTop;
              this.activeCensorPosition.style.width = startWidth;
              this.activeCensorPosition.style.height = startHeight;
              handleCleanup();
            }

            function handleEscKeyPress(keyupEvent) {
              if (keyupEvent.defaultPrevented) {
                return;
              }
              if (keyupEvent.key === "Escape") {
                resizeCancelled = true;
                this.activeCensorPosition.style.left = startLeft;
                this.activeCensorPosition.style.top = startTop;
                this.activeCensorPosition.style.width = startWidth;
                this.activeCensorPosition.style.height = startHeight;
                point.removeEventListener("pointermove", onMove);
                document.removeEventListener("keyup", handleEscKeyPress);
              }
            }

            document.addEventListener("keyup", handleEscKeyPress.bind(this));
            point.addEventListener("pointercancel", onCancel.bind(this));
            point.addEventListener("pointerup", onPointerUp.bind(this));
            point.addEventListener("pointermove", onMove);
          });

          this.activeCensorPosition.appendChild(point);
        }

        this.activeCensorPosition.addEventListener("pointerdown", this.handleCensorPointerDown.bind(this));
      }
    }

    handleFocusChangeAwayFromCensorType() {
      this.annotationBox.removeEventListener("click", this.annotationBoxCensorListener);
      this.annotationBox.className = "annotation-box";
      if (this.activeCensorPosition) {
        this.activeCensorPosition.removeEventListener("pointerdown", this.handleCensorPointerDown);
        this.activeCensorPosition.classList.toggle("active-censor-position");
      }
      const censorPositionAdjustmentPoints = document.getElementsByClassName("censor-position-adjustment-point");
      // iteration by index prevents unexpected behavior from deleting earlier elements
      // in the array.
      for (let pointI = censorPositionAdjustmentPoints.length - 1; pointI >= 0; pointI--) {
        const pointToRemove = censorPositionAdjustmentPoints[pointI];
        pointToRemove.remove();
      }
      this.activeCensorPosition = null;
    }

    changeAnnotationInFocus() {
      const previousTypeInFocus = this.typeOfAnnotationInFocus;
      const itemForm = document.getElementById("existing-item-form");
      if (itemForm == null) {
        this.typeOfAnnotationInFocus = null;
        return;
      }

      this.typeOfAnnotationInFocus = itemForm.dataset["annotationType"];
      this.annotationIdInFocus = itemForm.dataset["annotationId"];

      if (previousTypeInFocus == "censor") {
        this.handleFocusChangeAwayFromCensorType();
      }

      if (this.typeOfAnnotationInFocus == "censor" ) {
        this.handleFocusChangeToCensorType();
      }
    }

    async updateAnnotation({annotationType, annotationId, name=undefined, description=undefined, startTime=undefined, endTime=undefined, trackId=undefined, isFromItem=false, autoUpdateItem=true, autoUpdateForm=true}) {

      let requestBody, contentType;
      if (isFromItem) {
        requestBody = JSON.stringify({
          "content_id": this.contentId,
          "name": name,
          "description": description,
          "start_time": startTime,
          "end_time": endTime,
          "track_id": trackId,
        });
        contentType = "application/json";
      } else {
        const annotationUpdateForm = document.getElementById("annotation-update-form");
        const formData = new FormData(annotationUpdateForm);
        requestBody = {};
        for (let pair of formData.entries()) {
          const key = pair[0];
          const value = pair[1];
          requestBody[key] = value;
        }
        requestBody = JSON.stringify(requestBody);
        contentType = "application/json";
      }

      const response = await fetch(`/annotations/${annotationType}/${annotationId}/update/`, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": contentType,
        },
        body: requestBody
      });

      if (!response.ok) {
        console.error("An error occurred while updating an annotation");
        return false;
      }


      const responseData = await response.json();

      const itemHtml = responseData["item_html"];
      const formHtml = responseData["form_html"];

      if (autoUpdateItem) {
        const targetItem = document.getElementById(`${annotationType}-${annotationId}`);
        targetItem.outerHTML = itemHtml;

        this.placeTrackItems();
      }

      if (autoUpdateForm) {
        const targetForm = document.getElementById("detail-form");
        targetForm.innerHTML = formHtml;
        window.dispatchEvent(this.annotationUpdatedEvent);
      }
      return true;
    }

    triggerSave(state) {
        const item = state.item;
        const annotationType = item.dataset["annotationType"];
        const annotationId = item.dataset["annotationId"];

        const stateTypeIsUnknown = state.type !== "resize" && state.type !== "drag";
        const startAndEndTimesAreUnknown = !item.style.left || item.style.left == '' || !item.style.width || item.style.width == '';

        if (stateTypeIsUnknown) {
          console.error("Unknown state type:", state.type);
        }

        if (startAndEndTimesAreUnknown) {
          console.error("Could not determine item start and end times");
        }

        if (stateTypeIsUnknown || startAndEndTimesAreUnknown) {
          // Revert on error
          item.style.left = `${state.originalLeft}%`;
          item.style.width = `${state.originalWidth}%`;
          item.dataset.deltaLeft = '0';
          item.dataset.deltaWidth = '0';
          return;
        }

        const leftAsDecimal = convertPercentStringToDecimal(item.style.left)
        if (isNaN(leftAsDecimal)) {
          console.error(`Unable to parse item's left position: ${item.style.left}`);
          return;
        }
        const newStartTime = leftAsDecimal * this.duration;
        const widthAsDecimal = convertPercentStringToDecimal(item.style.width)
        if (isNaN(widthAsDecimal)) {
          console.error(`Unable to parse item's width: ${item.style.width}`);
          return;
        }
        const newEndTime = (leftAsDecimal + widthAsDecimal) * this.duration;
        this.updateAnnotation({annotationType, annotationId, startTime: newStartTime, endTime: newEndTime, isFromItem: true});
    }

    setTimeFromVideo(fieldName) {
        const video = document.querySelector('.annotation-player-container video');
        if (!video) return;

        const currentTime = video.currentTime;
        const timeString = this.secondsToHMS(currentTime);

        // Update the form field
        const input = document.getElementById(fieldName);
        if (input) {
            input.value = timeString;
        }
    }

    secondsToHMS(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }


    markCensorPositionAsActive(positionId) {
      // inactivate any active form elements
      const activeFormPositionCSSClass = "active-position-entry";
      const currentActiveFormPositions = document.querySelectorAll(`.${activeFormPositionCSSClass}`);
      for (let activePosition of currentActiveFormPositions) {
        activePosition.classList.remove(activeFormPositionCSSClass);
      }

      // activate form element
      const formPositionToActivate = document.querySelector(`.position-entry[data-position-id="${positionId}"]`);
      if (formPositionToActivate) {
        formPositionToActivate.classList.add(activeFormPositionCSSClass);
      }

      // inactivate any active position locators
      const activePositionLocatorCSSClass = "active-censor-position-locator";
      const currentActivePositionLocators = document.querySelectorAll(`.${activePositionLocatorCSSClass}`);
      for (let activePositionLocator of currentActivePositionLocators) {
        activePositionLocator.classList.remove(activePositionLocatorCSSClass);
      }

      // activeate position locator
      const positionLocatorToActivate = document.querySelector(`.censor-position-locator[data-position-id="${positionId}"]`);
      if (positionLocatorToActivate) {
        positionLocatorToActivate.classList.add(activePositionLocatorCSSClass);
      }
    }

    setupCensorPositionSeekListeners() {
      const handler = (clickEvent) => {
        const parent = clickEvent.target.closest(".position-entry");
        if (!parent) {
          return;
        }
        const timeInput = parent.querySelector(".position-time-input");
        let time = 0;
        if (timeInput) {
          time = timeInput.value;
          if (isNaN(Number(time))) {
            const form = clickEvent.target.closest("#annotation-update-form");
            const annotationStartInput = form.querySelector("#start_time");
            time = annotationStartInput.value;
          }
        }
        this.video.currentTime = time;
        this.markCensorPositionAsActive(parent.dataset["positionId"]);
      }
      const positionEntries = document.getElementsByClassName("position-entry");
      for (let positionEntry of positionEntries) {
        positionEntry.addEventListener("click", handler);
      }
    }

    setUpCensorPositionDeleteListeners(parentAnnotationId) {
      const buttons = document.getElementsByClassName("censor-position-delete-button");
      for (let button of buttons) {
        const buttonParent = button.parentElement;
        const positionId = buttonParent.dataset["positionId"];

        button.addEventListener("click", async () => {
          await this.deleteCensorPosition(parentAnnotationId, positionId);
        });
      }
    }

    setUpCommentChangeListeners(formElement) {
      // You may wonder why commentTextBox is declared in both event listeners instead of
      // outside them. This is because the box often does not generate quickly enough for
      // it to be defined before we query for it in the outer function. If you wait to get
      // it when the event fires, AnnotationPlayer.js has plenty of time to build it.
      const itemForm = formElement.querySelector("#existing-item-form");
      const annotationId = itemForm.dataset["annotationId"];
      const fontSizeInput = formElement.querySelector("#font-size");
      function getCommentBoxOrWriteError() {
        const commentTextBox = document.getElementById("comment-text-box-" + annotationId);
        if (!commentTextBox) {
          console.error("could not find comment text box with annotation id: " + annotationId);
          return undefined;
        }
        return commentTextBox;
      }
      const update = async () => {
        await this.updateAnnotation({annotationType: "comment", annotationId, autoUpdateForm: false})
      }

      // handle font size change
      fontSizeInput.addEventListener("input", () => {
        const commentTextBox = getCommentBoxOrWriteError();
        if (!commentTextBox) return;

        const newFontSize = fontSizeInput.value;
        if (newFontSize != undefined && newFontSize != '') {
          commentTextBox.style.fontSize = newFontSize + 'rem';
          update();
        }
      });

      // handle font color change
      const fontColorInput = itemForm.querySelector("#font-color");
      fontColorInput.addEventListener("input", () => {
        const commentTextBox = getCommentBoxOrWriteError();
        if (!commentTextBox) return;

        const newFontColor = fontColorInput.value;
        const newLength = newFontColor.length;
        if (newLength != 3 && newLength != 6) {
          return;
        }
        else {
          commentTextBox.style.color = "#" + fontColorInput.value;
          update();
        }
      });

      // handle top left x change
      const topX = itemForm.querySelector("#top-x");
      topX.addEventListener("input", () => {
        const commentTextBox = getCommentBoxOrWriteError();
        if (!commentTextBox) return;

        commentTextBox.style.left = parseFloat(topX.value) + '%';
        update();
      });

      // handle top left y change
      const topY = itemForm.querySelector("#top-y");
      topY.addEventListener("input", () => {
        const commentTextBox = getCommentBoxOrWriteError();
        if (!commentTextBox) return;

        commentTextBox.style.top = parseFloat(topY.value) + '%';
        update();
      });

      // handle bottom right x change
      const bottomX = itemForm.querySelector("#bottom-x");
      bottomX.addEventListener("input", () => {
        const commentTextBox = getCommentBoxOrWriteError();
        if (!commentTextBox) return;

        commentTextBox.style.width = (parseFloat(bottomX.value) - parseFloat(commentTextBox.style.left)) + '%';
        update();
      });

      // handle bottom right y change
      const bottomY = itemForm.querySelector("#bottom-y");
      bottomY.addEventListener("input", () => {
        const commentTextBox = getCommentBoxOrWriteError();
        if (!commentTextBox) return;

        commentTextBox.style.height = (parseFloat(bottomY.value) - parseFloat(commentTextBox.style.top)) + '%';
        update();
      });
    }

    cleanUpActiveCommentBoxes() {
      const commentBoxes = document.getElementsByClassName("comment-text-box");
      for (let box of commentBoxes) {
        box.classList.remove("comment-text-box-editor-active");
        const sizeControls = box.querySelectorAll(".comment-text-box-size-control");
        for (let control of sizeControls) {
          control.remove();
        }
      }
    }

    updateCommentBoxPositionAndSize(annotationId) {
      // validate that the box exists and we are editing the correct one
      const commentBox = document.getElementById("comment-text-box-" + annotationId);
      const updateForm = document.getElementById("annotation-update-form");
      if (!commentBox || !updateForm || updateForm.dataset["annotationId"] != annotationId) return;

      // update the form and save
      const boxTop = parseFloat(commentBox.style.top);
      const boxLeft = parseFloat(commentBox.style.left);

      const formTopX = updateForm.querySelector("#top-x");
      const formTopY = updateForm.querySelector("#top-y");
      const formBottomX = updateForm.querySelector("#bottom-x");
      const formBottomY = updateForm.querySelector("#bottom-y");
      if (!formTopX || !formTopY || !formBottomX || !formBottomY) {
        console.error("Failed to get all comment form elements to update");
        return;
      }

      // save original values in case update request goes wrong

      formTopX.value = boxLeft;
      formTopY.value = boxTop;
      formBottomX.value = boxLeft + parseFloat(commentBox.style.width);
      formBottomY.value = boxTop + parseFloat(commentBox.style.height);

      this.updateAnnotation({annotationType:"comment", annotationId, autoUpdateItem: false});
    }

    presentCommentBoxPositionAndSizeControls(annotationId) {
      this.cleanUpActiveCommentBoxes();
      const commentBox = document.getElementById("comment-text-box-" + annotationId);
      if (!commentBox) {
        return;
      }
      commentBox.classList.add("comment-text-box-editor-active");
      if (!commentBox) {
        console.log("No comment text box found for annotation id: " + annotationId);
        return;
      }

      // set up commentBoxDrag
      if (commentBox.dataset["setup"] == "false") {
        commentBox.addEventListener("pointerdown", (event) => {
          event.stopPropagation();
          commentBox.setPointerCapture(event.pointerId);
          const moveHandler = this.buildMoveHandler(commentBox);
          commentBox.addEventListener("pointermove", moveHandler);
          commentBox.addEventListener("pointerup", (event) => {
            this.updateCommentBoxPositionAndSize(annotationId);
            event.target.removeEventListener("pointermove", moveHandler);
          }, {once: true});
        });
        commentBox.dataset["setup"] = "true";
      }

      // set up controls
      const topControlClass = "resize-point-top";
      const leftControlClass = "resize-point-left";
      for (let i = 0; i < 4; i++) {
        // the box will have 4 controls, one in each corner. The top two (from left to right) are
        // i = 0 and i = 1. The bottom two (from left to right) are i = 2 and i = 3
        const isTop = i < 2;
        const isLeft = !(i % 2);
        const newDragControl = document.createElement("div");
        commentBox.appendChild(newDragControl);
        newDragControl.classList.add("comment-text-box-size-control");
        newDragControl.classList.add("resize-point");
        if (isTop) {
          newDragControl.classList.add(topControlClass);
        }
        if (isLeft) {
          newDragControl.classList.add(leftControlClass);
        }
        const moveHandler = this.buildResizePointMoveHandler();
        newDragControl.addEventListener("pointerdown", (event) => {
          event.stopPropagation();
          newDragControl.setPointerCapture(event.pointerId);
          newDragControl.addEventListener("pointermove", moveHandler);
          newDragControl.addEventListener("pointerup", () => {
            this.updateCommentBoxPositionAndSize(annotationId);
            newDragControl.removeEventListener("pointermove", moveHandler)
          }, {once: true})
        });
      }
    }

    async getItemFormDetails(annotationType, annotationId, contentId) {
      const response = await fetch(`/annotations/${annotationType}/${annotationId}/form/?content_id=${contentId}`, {
        method: "GET"
      });
      const detailForm = document.getElementById("detail-form");
      detailForm.innerHTML = await response.text();
      this.setUpItemForm();
      if (annotationType == "censor") {
        this.setUpCensorPositionDeleteListeners(annotationId);
        this.setupCensorPositionSeekListeners();
      }
      else if (annotationType == "comment") {
        this.setUpCommentChangeListeners(detailForm);
        this.presentCommentBoxPositionAndSizeControls(annotationId);
      }
      else if (annotationType == "clip") {
        // set up the next/previous clip buttons each time the form is generated
        function handleClipChange(isAdvancing) {
          // use 1 to advance forward, -1 to advance backward
          const advanceValue = isAdvancing ? 1 : -1;

          // we need to know where we are so we can determine which clip to click on.
          const clipList = document.getElementById("clip-annotation-items-list");
          const clipLen = clipList.children.length;
          let currentIndex;
          for (let index = 0; index < clipLen; index++) {
            const child = clipList.children[index];
            if (child.dataset["annotationId"] == annotationId) {
              currentIndex = index;
              break;
            }
          }

          if (currentIndex === undefined) {
            console.error(`Could not find clip annotation id (${annotationId}) matching the currently active clip.`);
            return;
          }

          const nextIndex = currentIndex + advanceValue;
          if (nextIndex >= clipLen) {
            // roll over to start of list
            clipList.children[0].click();
          }
          else if (nextIndex < 0) {
            clipList.children[clipLen - 1].click();
          }
          else {
            clipList.children[nextIndex].click();
          }
        }
        const nextClipButton = document.getElementById("next-clip-button");
        if (nextClipButton) {
          nextClipButton.addEventListener("click", () => handleClipChange(true));
        }
        const lastClipButton = document.getElementById("last-clip-button");
        if (lastClipButton) {
          lastClipButton.addEventListener("click", () => handleClipChange(false));
        }
      }
    }

    markItemAsActive(annotationType, annotationId) {
      if (!annotationType || !annotationId) {
        console.error("Invalid annotation type of annotation id");
        return;
      }

      // handle panel item style
      const activePanelItemClass = "active-panel-item";
      const itemListExpansionClass = "annotation-type-list-expanded";
      const arrowRotationClass = "annotation-header-arrow-rotated";
      const thisPanelItem = document.getElementById(`${annotationType}-panel-item-${annotationId}`);

      if (!thisPanelItem) {
        console.error("Failed to identify the focused panel item");
        return;
      }

      const activePanelItems = document.getElementsByClassName(activePanelItemClass);
      for (let activePanelItem of activePanelItems) {
        const activePanelItemType = activePanelItem.dataset["annotationType"];
        const panelTypeClassToDisable = `${activePanelItemType}-list-item-wrapper-selected`;
        activePanelItem.classList.remove(panelTypeClassToDisable, activePanelItemClass);
        // we want to collapse the item panel if the new active item is not the same type as the old one.
        if (activePanelItemType != annotationType) {
          const parentItemList = activePanelItem.closest(".annotation-type-list");
          if(parentItemList) {
            parentItemList.classList.remove(itemListExpansionClass);
          }
          const annotationGroupWrapper = activePanelItem.closest(".annotation-type-wrapper");
          const groupWrapperArrow = annotationGroupWrapper.querySelector(".annotation-type-header-arrow");
          groupWrapperArrow.classList.remove(arrowRotationClass);
        }
      }

      // we are ready to add the active item's style
      thisPanelItem.classList.add(`${annotationType}-list-item-wrapper-selected`, activePanelItemClass);
      thisPanelItem.scrollIntoView({behavior: "smooth"});
      const thisItemList = thisPanelItem.closest(".annotation-type-list");
      thisItemList.classList.add(itemListExpansionClass);
      const thisGroupWrapper = thisPanelItem.closest(".annotation-type-wrapper");
      const thisGroupArrow = thisGroupWrapper.querySelector(".annotation-type-header-arrow");
      thisGroupArrow.classList.add(arrowRotationClass);

      // handle track item style
      const activeTrackItemCSSClass = "active-track-item";
      // first turn off styling of current active element
      const currentActiveTrackItem = this.timelineWrapper.querySelector(`.${activeTrackItemCSSClass}`);
      if (currentActiveTrackItem) {
        currentActiveTrackItem.classList.remove(activeTrackItemCSSClass);
      }

      const trackItem = this.timelineWrapper.querySelector(`.track-item[data-annotation-type="${annotationType}"][data-annotation-id="${annotationId}"]`);
      if (!trackItem) {
        console.error("No track item found");
        return;
      }
      trackItem.classList.add(activeTrackItemCSSClass);

      // skip to start of this annotation in video
      const startTime = Number(trackItem.dataset["start"]);
      if (startTime && !isNaN(startTime)) {
        this.video.currentTime = startTime;
      }
    }

    setUpPanelItemClickListeners() {
      const annotationPanelItems = document.getElementsByClassName("panel-item");
      for (let panelItem of annotationPanelItems) {
        this.setUpItemClickListeners(panelItem);
      }
    }

    setUpAnnotationPanelClickListeners() {
      const annotationPanelGroupHeaders = document.getElementsByClassName("annotation-type-header");
      const panelLists = document.getElementsByClassName("annotation-type-list");
      const panelArrows = document.getElementsByClassName("annotation-type-header-arrow");
      for (let panel of annotationPanelGroupHeaders) {
        const thisPanelList = panel.parentElement.querySelector(".annotation-type-list");
        const listClassName = "annotation-type-list-expanded";
        const thisPanelArrow = panel.querySelector(".annotation-type-header-arrow");
        const arrowClassName = "annotation-header-arrow-rotated";
        panel.addEventListener("click", () => {
          if (thisPanelList.className.includes(listClassName)) {
            thisPanelList.classList.remove(listClassName)
            thisPanelArrow.classList.remove(arrowClassName)
          }
          else {
            for (const arrow of panelArrows) {
              arrow.classList.remove(arrowClassName);
            }
            for (const panelList of panelLists) {
              panelList.classList.remove(listClassName);
            }
            thisPanelArrow.classList.add(arrowClassName);
            thisPanelList.classList.add(listClassName);
          }
        });
      }
    }

    adjustScrubberHeight() {
      const scrubberContainer = document.getElementById("timeline-row-ticks-and-scrubbers");
      const trackRows = document.getElementsByClassName("track-row")
      const scrubberContainerDim = scrubberContainer.getBoundingClientRect();
      const topOfScrubber = scrubberContainerDim.top;
      let bottomOfBottomTrack = scrubberContainerDim.bottom;
      for (let trackRow of trackRows) {
        const trackRowDim = trackRow.getBoundingClientRect();
        if (bottomOfBottomTrack < trackRowDim.bottom) {
          bottomOfBottomTrack = trackRowDim.bottom;
        }
      }
      const scrubbers = document.getElementsByClassName("vertical-scrubber");
      const newScrubberHeight = (bottomOfBottomTrack - topOfScrubber) + "px";
      for (let scrubber of scrubbers) {
        scrubber.style.height = newScrubberHeight;
      }
    }

    /* TRACK EVENT WATCHERS AND HANDLERS */
    setupTracks() {
      const trackRows = document.getElementsByClassName("track-row");
      for (let trackRow of trackRows) {
        this.setupTrackWatchers(trackRow);
      }

      const trackRowAnnotationContainers = document.getElementsByClassName("track-row-annotations-container");
      for (let annotationContainer of trackRowAnnotationContainers) {
        this.setupTrackDragListeners(annotationContainer);
      }
    }

    setupTrackDragListeners(annotationContainer) {
      // set up dragover behavior
      const timelineRow = annotationContainer.closest(".timeline-row");
      const thisContainerTrackId = timelineRow.dataset["trackId"];
      const projectionEl = document.createElement("div");
      projectionEl.classList.add("track-item-projection");
      projectionEl.style.visibility = "hidden";
      annotationContainer.appendChild(projectionEl);
      annotationContainer.addEventListener("dragenter", () => {
        projectionEl.style.visibility = "visible";
      });

      annotationContainer.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (!this.itemBeingDragged) {
          console.error("No item is being dragged!");
          return;
        }
        const itemOriginalTrackId = this.itemBeingDragged.dataset["originalTrackId"];
        projectionEl.style.width = this.itemBeingDragged.style.width;
        if (itemOriginalTrackId == thisContainerTrackId) {
          // show the projection moving in concert with the mouse
          const containerDim = annotationContainer.getBoundingClientRect();
          const newLeftRatio = (event.clientX - containerDim.left) / containerDim.width;
          this.video.currentTime = this.video.duration * newLeftRatio;
          projectionEl.style.left = (newLeftRatio * 100) + '%';
          projectionEl.style.top = this.itemBeingDragged.style.top;
        }
        else {
          // show the projection statically placed with same position as itemBeingDragged.
          this.video.currentTime = this.itemBeingDragged.dataset["start"];
          projectionEl.style.left = this.itemBeingDragged.style.left;
        }
      });

      annotationContainer.addEventListener("dragleave", (event) => {
        // check if executing on self or on this annotationContainer
        const fromItem = event.fromElement?.closest(".track-item");
        if (fromItem?.id == this.itemBeingDragged.id || event?.fromElement == annotationContainer) {
          return;
        }
        projectionEl.style.visibility = "hidden";
      });

      // set up drop behavior, should reject anything that isn't a trackItem
      const itemIdRegEx = new RegExp("[a-z]+-[0-9]+");
      annotationContainer.addEventListener("drop", async (event) => {
        event.preventDefault();
        this.itemBeingDragged = null;
        projectionEl.style.visibility = "hidden";
        // check if it has been longer than 50ms since drag started
        if ((Date.now() - this.dragTimeStart) <= 100) {
          return;
        }
        const itemId = event.dataTransfer.getData("text");
        if (!itemId || !itemIdRegEx.test(itemId)) {
          return;
        }
        const originalItem = document.getElementById(itemId);

        // build new item, and check if we need to move it to a different track
        const trackItemHTML = event.dataTransfer.getData("text/html");
        // the second child of the new element has to be used because the dataTransfer API
        // adds some meta information about the html as the root node in the transfered HTML
        const replacementItem = createElementFromHTMLString(trackItemHTML, 1);
        const trackRowParent = event.target.closest(".track-row");
        const trackId = trackRowParent.dataset["trackId"];
        const originalTrackId = originalItem.dataset["originalTrackId"];
        const annotationType = originalItem.dataset["annotationType"];
        const annotationId = originalItem.dataset["annotationId"];
        const originalStartTime = parseFloat(originalItem.dataset["start"]);
        let originalEndTime;
        if (originalItem.dataset["end"]) {
          originalEndTime = parseFloat(originalItem.dataset["end"]);
        }
        let success;
        if (trackId != originalTrackId) {
          // transfer item to new track

          success = await this.updateAnnotation({annotationType, annotationId, "isFromItem": true, "trackId": trackId, "startTime": originalStartTime, "endTime": originalEndTime, "autoUpdateItem": false});
          if (success) {
            replacementItem.dataset["originalTrackId"] = trackId;
          }
        } else {
          // move item to new position within same track
          const containerDim = annotationContainer.getBoundingClientRect();
          const newLeftRatio = (event.clientX - containerDim.left) / containerDim.width;
          const startTime = this.video.duration * newLeftRatio;
          let endTime;
          if (originalEndTime) {
            endTime = originalEndTime - originalStartTime + startTime;
          }
          success = await this.updateAnnotation({annotationType, annotationId, "isFromItem": true, "startTime": startTime, "endTime": endTime, "autoUpdateItem": false});
          if (success) {
            replacementItem.dataset["start"] = startTime;
            if (endTime) {
              replacementItem.dataset["end"] = endTime;
            }
            replacementItem.style.left = newLeftRatio * 100 + '%';
            if (annotationType == "censor") {
              const censorPositions = replacementItem.querySelectorAll(".censor-position-locator");
              const positionsToDelete = [];
              for (let position of censorPositions) {
                if (position.dataset["positionTime"] < startTime) {
                  positionsToDelete.push(position);
                }
              }

              for (let i = positionsToDelete.length - 1; i > -1; i--) {
                positionsToDelete[i].remove();
              }
            }
          }
        }

        if (success) {
          replacementItem.dataset["setup"] = "false";
          originalItem.remove();
          replacementItem.classList.remove("is-dragging");
          annotationContainer.appendChild(replacementItem);
          this.placeTrackItems();
        }
      });
    }

    // use this method to apply all relevant watchers (event listeners) to
    // tracks that are new to the DOM.
    setupTrackWatchers(trackRootElement) {
      this.watchForMultiSelectMenuOpen(trackRootElement);
      this.watchForDisplayTrackRename(trackRootElement);
      this.watchForTrackRename(trackRootElement);
      this.watchForTrackMovement(trackRootElement);
      this.watchForTrackDelete(trackRootElement);
    }

    /* track options menu */
    handleMultiSelectMenuOpen(e) {
      e.stopPropagation();
      // we don't want more than one track menu visible at one time
      const visibleMenuCSSClass = "visible-multi-select-menu";
      const allVisibleTrackMenus = document.getElementsByClassName(visibleMenuCSSClass);
      for (let menu of allVisibleTrackMenus) {
        menu.classList.remove(visibleMenuCSSClass);
      }

      // get track menu and position it properly
      const wrapperDim = this.timelineWrapper.getBoundingClientRect();
      const trackMenuWrapper = e.target.closest(".multi-select-menu-parent");
      const trackOptionsMenu = trackMenuWrapper.querySelector(".multi-select-menu");
      trackOptionsMenu.style.visibility = "hidden";
      trackOptionsMenu.classList.add(visibleMenuCSSClass);
      const trackMenuDim = trackOptionsMenu.getBoundingClientRect();
      if ((trackMenuDim.bottom - wrapperDim.bottom) > -20) {
        trackOptionsMenu.classList.add("multi-select-menu-bumped-up");
      }

      // now we are safe to make the track menu visible
      trackOptionsMenu.style.visibility = "";
    }

    watchForMultiSelectMenuOpen(multiSelectMenuWrapper) {
      const menuButton = multiSelectMenuWrapper.querySelector(".open-multi-select-button");
      if (menuButton) {
        menuButton.addEventListener("click", this.handleMultiSelectMenuOpen.bind(this));
      }
      else {
        console.error("No menu button element found");
      }
    }

    watchForClickOutsideOfTrackMenu() {
      const editorContainer = document.getElementById("editor-container");
      editorContainer.addEventListener("click", (e) => {
        const visibleTrackMenus = document.getElementsByClassName("visible-multi-select-menu");
        for (let menu of visibleTrackMenus) {
          const menuDim = menu.getBoundingClientRect();
          if (e.x < menuDim.left || e.x > menuDim.right || e.y < menuDim.top || e.y > menuDim.bottom) {
            menu.classList.remove("visible-multi-select-menu");
          }
        }
      });
    }

    replaceTracksWithNewHTML(newTracksHTML) {
      // remove old tracks
      const trackRows = document.getElementsByClassName("track-row");

      for (let i = trackRows.length - 1; i >= 0; i--) {
        trackRows[i].remove();
      }

      // place new tracks in appropriate positions
      const timelineZoomRow = document.getElementById("timeline-new-track-and-zoom-row");
      for (let newTrackHTML of newTracksHTML) {
        const trackParentNode = createElementFromHTMLString(newTrackHTML);
        this.setupTrackWatchers(trackParentNode);
        this.timelineWrapper.insertBefore(trackParentNode, timelineZoomRow);
      }
    }

    /* track name change */
    async handleTrackNameChange(e) {
      e.stopPropagation();
      let parentTrackRow = e.target.closest(".track-row");

      if (!parentTrackRow) {
        console.error("Failed to change track name due to undefined track row element");
        return;
      }

      const trackNameEl = parentTrackRow.querySelector(".track-name");
      const trackId = parentTrackRow.dataset["trackId"];

      function resetTrackName() {
        const renameWrapper = parentTrackRow.querySelector(".track-rename-wrapper");
        renameWrapper.classList.remove("track-rename-wrapper-visible");
        e.target.value = trackNameEl.innerText;
      }

      if (e.key == "Enter") {
        const newTrackName = e.target.value.trim();
        const response = await fetch("/track/update", {
          method: "post",
          headers: {"X-CSRFToken": getCSRFToken()},
          body: JSON.stringify({"new_track_name": newTrackName, "track_id": trackId})
        });
        if (!response.ok) {
          resetTrackName();
          return;
        }

        trackNameEl.innerText = newTrackName;
        e.target.value = trackNameEl.innerText;
      }
      if (e.key == "Escape") {
        resetTrackName();
        return;
      }
    }

    watchForTrackRename(trackRootElement) {
      const trackRenameInput = trackRootElement.querySelector(".track-rename-input");
      if (trackRenameInput) {
        trackRenameInput.addEventListener("keydown", this.handleTrackNameChange.bind(this));
      }
      else {
        console.error("No rename input found for track");
      }
    }

    // Handles renaming of track name, canceling attempted rename,
    // replacement of track with new track, and appending this same
    // event listener to the new track name edit button.
    displayTrackRenameField(e) {
      e.stopPropagation();
      const visibleRenameCSSClass = "track-rename-wrapper-visible";

      // close any other open rename fields
      const visibleRenameWrappers = document.getElementsByClassName(visibleRenameCSSClass);
      for (let wrapper of visibleRenameWrappers) {
        wrapper.classList.remove(visibleRenameCSSClass);
      }

      // open the rename wrapper we are interested in
      const parentRow = e.target.closest(".track-row");
      const renameWrapper = parentRow.querySelector(".track-rename-wrapper");
      const renameInput = renameWrapper.querySelector(".track-rename-input");
      renameWrapper.classList.add(visibleRenameCSSClass);
      renameInput.focus();
      renameInput.setSelectionRange(0, 50);
    }

    watchForDisplayTrackRename(trackRootElement) {
      const renameButton = trackRootElement.querySelector(".track-menu-rename");
      if (renameButton) {
        renameButton.addEventListener("click", this.displayTrackRenameField.bind(this))
      }
      else {
        console.error("No rename button found for track");
      }
    }

    /* track delete */
    async deleteTrack(e) {
      const trackRow = e.target.closest(".track-row");
      const trackId = trackRow.dataset["trackId"];
      const trackDeleteResponse = await fetch(`/track/delete/${trackId}`, {
        method: "delete",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        }
      });

      if (!trackDeleteResponse.ok) {
        console.error("Failed to delete track");
        return;
      }

      trackRow.remove();
      this.updateTracks();
    }

    watchForTrackDelete(trackRootElement) {
      const deleteButton = trackRootElement.querySelector(".track-menu-delete");
      if (deleteButton) {
        deleteButton.addEventListener("click", this.deleteTrack.bind(this))
      }
    }

    /* Track order reassignment */
    async handleTrackOrderReassignment(trackIdToMove, isMoveUp) {
      if (typeof(isMoveUp) != "boolean") {
        console.error("isMoveUp must be boolean type");
        return;
      }
      const trackRows = document.getElementsByClassName("track-row");
      const trackIdOrder = [];
      for (let trackRow of trackRows) {
        trackIdOrder.push(trackRow.dataset["trackId"]);
      }

      // we don't want to attempt to move the first track up, or the last track down
      if ((isMoveUp && trackIdOrder[0] == trackIdToMove) || (!isMoveUp && trackIdOrder[trackIdOrder.length - 1] == trackIdToMove)) {
        return;
      }

      // use isMoveUp to iterate over only the parts of the array that we care about.
      // Remember - we don't care to move the top row up, or the bottom row down
      for (let i = Number(isMoveUp); i < trackIdOrder.length - Number(!isMoveUp); i++) {
        const currentTrackId = trackIdOrder[i];
        if (currentTrackId == trackIdToMove) {
          if (isMoveUp) {
            const swappingTrackId = trackIdOrder[i - 1];
            trackIdOrder[i - 1] = currentTrackId;
            trackIdOrder[i] = swappingTrackId;
          }
          else {
            const swappingTrackId = trackIdOrder[i + 1];
            trackIdOrder[i + 1] = currentTrackId;
            trackIdOrder[i] = swappingTrackId
          }
          break;
        }
      }

      const orderUpdateResponse = await fetch("/tracks/update_stack_positions",
        {
          method: "post",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            track_ids: trackIdOrder
          })
        }
      );

      if (!orderUpdateResponse.ok) {
        console.error("Failed to update stack positions across all tracks");
        return;
      }
      const jsonData = await orderUpdateResponse.json();
      const newTracksHTML = jsonData["tracks_html"];

      this.replaceTracksWithNewHTML(newTracksHTML);
      this.updateTracks();
      this.placeTrackItems();
    }

    watchForTrackMovement(trackRootElement) {
      const trackId = trackRootElement.dataset["trackId"];
      const moveUpButton = trackRootElement.querySelector(".track-menu-move-up");

      if (moveUpButton) {
        const handleMoveUp = () => {this.handleTrackOrderReassignment(trackId, true)};
        moveUpButton.addEventListener("click", handleMoveUp.bind(this));
      }

      const moveDownButton = trackRootElement.querySelector(".track-menu-move-down");
      if (moveDownButton) {
        const handleMoveDown = () => {this.handleTrackOrderReassignment(trackId, false)};
        moveDownButton.addEventListener("click", handleMoveDown.bind(this));
      }
    }

    /* track creation */
    watchForTrackCreation() {
      const addNewTrackButton = document.getElementById("new-track-save-button");
      const addNewTrackInput = document.getElementById("new-track-name");
      const dialog = document.getElementById("new-track-dialog");
      const annotationSetId = this.timelineWrapper.dataset["annotationSetId"];

      async function handleTrackCreation(e) {
        e.stopPropagation();
        // allow any event unless it is keydown triggered from a key other than the Enter key
        if (e.type == "keydown" && e.key != "Enter") {
          return;
        }
        const newTrackName = addNewTrackInput.value;
        if (!newTrackName) {
          return;
        }

        const newTrackResponse = await fetch("/track/create", {
          method: "post",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            "annotation_set_id": annotationSetId,
            "track_name": newTrackName
          })
        });

        dialog.close();

        if (!newTrackResponse.ok) {
          console.error("Failed to create new track!");
          return;
        }

        addNewTrackInput.value = "";

        const responseData = await newTrackResponse.json();
        const newTracksHTML = responseData["tracks_html"];

        this.replaceTracksWithNewHTML(newTracksHTML);
        this.updateTracks();
        this.placeTrackItems();
        this.adjustScrubberHeight();
      }
      addNewTrackButton.addEventListener("click", handleTrackCreation.bind(this));
      addNewTrackInput.addEventListener("keydown", (e) => {
        if (e.key == "Enter") {
          addNewTrackButton.click();
        }
      });
    }

    placeTrackItems() {
        // Process each track container separately
        this.tracks.forEach(track => {
            const trackItems = Array.from(track.children);
            for (let item of trackItems) {
              const itemStart = parseFloat(item.dataset["start"]);
              const itemEnd = parseFloat(item.dataset["end"]);
              const itemDuration = itemEnd - itemStart;

              if (item.dataset.annotationType != "pause") {
                const itemWidthValue = itemDuration / this.duration * 100;
                item.style.setProperty("width", `${itemWidthValue}%`);
                this.hideOrShowResizeHandles(item);
              }
              const itemLeftValue = parseFloat(item.dataset["start"]) / this.duration * 100
              item.style.setProperty("left", `${itemLeftValue}%`);

              // apply postion styling to censor positions
              if (item.dataset.annotationType == "censor") {
                const censorPositionLocators = item.querySelectorAll(".censor-position-locator");
                for (let positionLocator of censorPositionLocators) {
                  const positionLocatorDim = positionLocator.getBoundingClientRect();
                  const positionWidth = positionLocatorDim.width;
                  const positionTime = positionLocator.dataset["positionTime"];
                  const leftValue = ((positionTime - itemStart) / (itemEnd - itemStart)) * 100;
                  positionLocator.style.setProperty("left", `calc(${leftValue}% - ${positionWidth / 2 - 2}px`);
                }
              }
            }
            const trackDim = track.getBoundingClientRect();
            const itemCount = trackItems.length;

            // Track the bottom of each row (stack)
            let rowBottoms = [];

            // place each track item
            for (let itemIndex = 0; itemIndex < itemCount; itemIndex++) {
                const currentTrackItem = trackItems[itemIndex];
                const currentItemStart = Number(currentTrackItem.dataset.start);
                const currentItemEnd = Number(currentTrackItem.dataset.end);
                const currentTrackItemDim = currentTrackItem.getBoundingClientRect();

                // find the lowest positioned overlapping sibling so we know where to place currentTrackItem
                let allOverlappingSiblings = [];
                let lowestPositionedOverlappingSibling;
                for (let siblingItemIndex = 0; siblingItemIndex < itemIndex; siblingItemIndex++) {
                    const siblingItem = trackItems[siblingItemIndex];
                    if (siblingItem == currentTrackItem) { // this should never happen
                        break;
                    }
                    const siblingItemStart = Number(siblingItem.dataset.start);
                    const siblingItemEnd = Number(siblingItem.dataset.end);
                    // sibling can be smaller and completely overlap with current item
                    // sibling can be larger and completely overlap with current item
                    // sibling can overlap with current item on current item start but not end time
                    // sibling can overlap with current item on item end but not start time
                    const isOverlapping = ((currentItemStart <= siblingItemStart && currentItemEnd >= siblingItemEnd)
                        || (currentItemStart >= siblingItemStart && currentItemEnd <= siblingItemEnd)
                        || (currentItemStart >= siblingItemStart && currentItemStart <= siblingItemEnd)
                        || (currentItemEnd >= siblingItemStart && currentItemEnd <= siblingItemEnd));
                    if (isOverlapping) {
                        allOverlappingSiblings.push(siblingItem);
                        let lowestSiblingDim;
                        const siblingDim = siblingItem.getBoundingClientRect();
                        if (lowestPositionedOverlappingSibling) {
                            lowestSiblingDim = lowestPositionedOverlappingSibling.getBoundingClientRect();
                        }

                        if (!lowestSiblingDim) {
                            lowestPositionedOverlappingSibling = siblingItem;
                        }
                        else if(lowestSiblingDim.bottom < siblingDim.bottom) {
                            lowestPositionedOverlappingSibling = siblingItem;
                        }
                    }
                }

                // place currentTrackItem if there is an overlapping sibling
                if (lowestPositionedOverlappingSibling) {
                    // check if there is room at the top
                    let isSiblingOccupyingTopSpot = false;
                    for (let sibling of allOverlappingSiblings) {
                        const overLapSibDim = sibling.getBoundingClientRect();
                        if (overLapSibDim.bottom - trackDim.top <= 35) {
                            isSiblingOccupyingTopSpot = true;
                            break;
                        }
                    }
                    if (isSiblingOccupyingTopSpot) {
                        // take the bottom of sibling, subtract the top of the container, add 5 pixels
                        const siblingDim = lowestPositionedOverlappingSibling.getBoundingClientRect();
                        currentTrackItem.style.top = siblingDim.bottom - trackDim.top + 5 + "px";
                    }
                } else {
                    // else place at the top (default)
                    currentTrackItem.style.top = "5px";
                }

                // Track the bottom of this item for stacking calculation
                const itemTop = parseFloat(currentTrackItem.style.top) || 0;
                const itemBottom = itemTop + currentTrackItemDim.height; // item height is 30px
                rowBottoms.push(itemBottom);
            }

            // Calculate the number of stacked rows (find max top value / 35 + 1)
            let maxStack = 1;
            if (trackItems.length > 0) {
                // Find all unique top positions (rounded to nearest 5px)
                const tops = trackItems.map(item => Math.round((parseFloat(item.style.top) || 0) / 5) * 5);
                const uniqueRows = Array.from(new Set(tops));
                maxStack = uniqueRows.length;
            }

            // Set min-height on the parent track (timeline-row)
            const trackContainer = track.closest('.timeline-row');
            if (trackContainer) {
                // 1 item = 40px; 2 = 75px; 3 = 110px; 4 = 145px; etc. (diff = 35px)
                const minHeight = (maxStack * 35) + 5;
                trackContainer.style.minHeight = `${minHeight}px`;
            }
        });
      this.adjustScrubberHeight();
      this.setupItems();
      this.setUpPanelItemClickListeners();
    }

    handleTrackItemPlacementAfterEvent(e) {
        const classList = e.detail.target.classList;
        const id = e.detail.target.id;
        if (classList.contains('track-items') || classList.contains("track-item") || classList.contains("detail-form") || id.includes("item-")) {
            this.placeTrackItems();
        }
    }

    listenForNewItemCreation() {
      const assignListeners = (elements) => {
        for (let button of elements) {
          const annotationType = button.dataset["annotationType"];
          button.addEventListener("click", async (e) => {
            e.preventDefault();
            const trackRow = document.querySelector(".track-row");
            if (!trackRow) {
              console.error("Unable to assign listeners to new item creation buttons. Invalid track row.");
              return;
            }
            const trackId = trackRow.dataset["trackId"];

            let startTime = 0;
            let endTime = 0;
            if (this.video) {
                startTime = this.video.currentTime;
                // Make sure new item can fit on the page
                const itemDuration = Math.min(this.duration * 0.2, 10);
                endTime = Math.min(startTime + itemDuration, this.duration);
            }

            const response = await fetch(`/annotations/${annotationType}/create/track/${trackId}`,
              {
                method: "POST",
                headers: {
                  "X-CSRFToken": getCSRFToken(),
                  "Content-Type": "application/json"
                },
                body: JSON.stringify({
                  "start_time": startTime,
                  "end_time": endTime
                })
              });
            if (response.ok) {
              const parsedResponse = await response.json();
              const newPanelItemHtml = parsedResponse["panel_item_html"];
              const panel = document.getElementById(`${annotationType}-annotation-items-list`);
              panel.innerHTML = panel.innerHTML + newPanelItemHtml;

              const newTrackItemHtml = parsedResponse["track_item_html"];
              const trackContainer = document.querySelector(`.track-row[data-track-id="${trackId}"] .track-row-annotations-container`);
              const newNode = createElementFromHTMLString(newTrackItemHtml);
              newNode.dataset["start"] = startTime;
              newNode.dataset["end"] = endTime;
              trackContainer.appendChild(newNode);
              this.placeTrackItems();
              window.dispatchEvent(this.annotationUpdatedEvent);
            }
            else {
              console.error(response);
            }
          })
        }
      };
      const createItemButtons = document.getElementsByClassName("add-item-btn");
      const panelCreateItemButtons = document.getElementsByClassName("annotation-type-add-button");
      assignListeners(createItemButtons);
      assignListeners(panelCreateItemButtons);
    }

    /* TIMELINE FUNCTIONS */
    // This is the tick line on the timeline
    createTickMark(time, isMajor) {
        const tick = document.createElement('div');
        tick.className = `tick-mark ${isMajor ? 'major' : 'minor'}`;

        const percent = (time / this.duration) * 100;
        tick.style.left = `${percent}%`;

        return tick;
    }

    formatTime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        if (hours > 0) {
            return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
        } else if (minutes > 0) {
            return `${minutes}:${String(secs).padStart(2, '0')}`;
        } else {
            return `0:${String(secs).padStart(2, '0')}`;
        }
    }

    // this is for the time stamp above each labeled tick line on the timeline
    createTickLabel(time) {
        const label = document.createElement('div');
        label.className = 'tick-label';
        label.textContent = this.formatTime(time);

        const percent = (time / this.duration) * 100;
        if (percent == 0) {
          label.className += " first-tick";
        }
        else {
          label.style.left = `${percent}%`;
        }

        return label;
    }

    calculateTickInterval() {
        // Calculate how many seconds fit in viewport at current zoom
        const viewportSeconds = this.duration / this.zoomLevel;

        // Choose interval to show 4-6 labels
        const targetLabels = 5;
        const rawInterval = viewportSeconds / targetLabels;

        // Snap to nice intervals: 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, etc.
        const niceIntervals = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600];

        for (const interval of niceIntervals) {
            if (interval >= rawInterval) {
                return interval;
            }
        }

        // For very long videos, use multiples of an hour
        return Math.ceil(rawInterval / 3600) * 3600;
    }

    renderTickMarksAndLabels() {
        if (!this.tickMarksContainer) return;

        // Clear existing tick marks
        this.tickMarksContainer.innerHTML = '';

        // Calculate appropriate interval based on zoom and duration
        const interval = this.calculateTickInterval();
        const minorInterval = interval / 5;

        // Generate tick marks
        for (let time = 0; time <= this.duration; time += minorInterval) {
            const compTime = time.toFixed(2);
            const isMajor = Math.abs(compTime % interval) < 0.01;
            const tick = this.createTickMark(compTime, isMajor);
            this.tickMarksContainer.appendChild(tick);

            // Add label for major ticks
            if (isMajor) {
                const label = this.createTickLabel(compTime);
                this.tickMarksContainer.appendChild(label);
            }
        }
    }

    // Recompute cached timeline geometry, then position the scrubber. This reads
    // layout (getBoundingClientRect/scrollLeft), so it is only called on the
    // events that can change the geometry (zoom, scroll, seek, resize, and the
    // periodic timeupdate) — never per animation frame.
    adjustScrubberPosition() {
      this.refreshScrubberBounds();
      this.paintScrubber();
    }

    /* The scrubber is bound between the left and right edges of the visible
    window; it never goes off the page. Its position depends on the current time
    and how far the timeline is zoomed/scrolled. We can think of the currently
    displayed time as a window on the full timeline. These bounds only change on
    zoom/scroll/resize, so we cache them and let paintScrubber() interpolate. */
    refreshScrubberBounds() {
      const totalTimeline = this.tickMarksContainer;
      const visibleTimeline = this.timelineTicksContent;
      if (!totalTimeline || !visibleTimeline) return;

      const totalScrollableWidth = totalTimeline.getBoundingClientRect().width;
      const windowWidth = visibleTimeline.getBoundingClientRect().width;
      const scrollLeft = visibleTimeline.scrollLeft;

      this.scrubberBounds = {
        windowWidth,
        leftTime: this.video.duration * (scrollLeft / totalScrollableWidth),
        rightTime: this.video.duration * ((scrollLeft + windowWidth) / totalScrollableWidth),
      };
    }

    // Position the scrubber from the cached bounds and the current time. Uses a
    // composited transform and performs no layout reads, so it is cheap enough
    // to run every animation frame during playback.
    paintScrubber() {
      if (!this.scrubberBounds || !this.editorScrubber) return;

      const { windowWidth, leftTime, rightTime } = this.scrubberBounds;
      const span = rightTime - leftTime;
      const currentTime = this.video.currentTime;

      let ratio;
      if (span <= 0 || currentTime <= leftTime) {
        ratio = 0;
      } else if (currentTime >= rightTime) {
        ratio = 1;
      } else {
        ratio = (currentTime - leftTime) / span;
      }

      this.editorScrubber.style.transform = `translateX(${ratio * windowWidth}px)`;
    }

    handleZoom() {
      const annotationContainers = this.timelineWrapper.querySelectorAll(".track-row-annotations-container");
      if (annotationContainers.length == 0) {
        return;
      }

      const newWidth = `${100 * this.zoomLevel}%`;
      const zoomSliderWidth = `${100 / this.zoomLevel}%`;
      document.documentElement.style.setProperty("--zoom-thumb-width", zoomSliderWidth);
      const tickMarksContainer = document.getElementById("tick-marks-container");
      if (tickMarksContainer) {
        tickMarksContainer.style.width = newWidth;
      }
      for (let annotationContainer of annotationContainers) {
        annotationContainer.style.width = newWidth;
      }

      const locationRatio = this.video.currentTime / this.video.duration;
      const trackWidth = tickMarksContainer.getBoundingClientRect().width;
      const tickMarkContainerParent = tickMarksContainer.closest("#timeline-ticks-content");
      const parentWidth = tickMarkContainerParent.getBoundingClientRect().width;
      this.scrollTracksToPoint((trackWidth * locationRatio) - parentWidth / 2);
      this.renderTickMarksAndLabels();
      // Zoom changes the visible time window, so recompute the scrubber bounds.
      this.adjustScrubberPosition();
    }

    attachZoomListener() {
      const bindZoomLevel = () => {
        if (this.zoomLevel > 10) {
          console.error("zoomLevel exceeded expected max zoom of 10; reverting to zoom 10.");
          this.zoomLevel = 10;
        }
        else if (this.zoomLevel < 1) {
          console.error("zoomLevel lower than expected min zoom of 1; reverting to zoom 1.");
          this.zoomLevel = 1;
        }
      }

      const zoomInButton = document.getElementById("zoom-in-button");
      if (zoomInButton) {
        zoomInButton.addEventListener("click", () => {
          if (this.zoomLevel < 10) {
            this.zoomLevel += 1;
          }
          bindZoomLevel();
          this.handleZoom();
        });
      }

      const zoomOutButton = document.getElementById("zoom-out-button");
      if (zoomOutButton) {
        zoomOutButton.addEventListener("click", () => {
          if (this.zoomLevel > 1) {
            this.zoomLevel -= 1;
          }
          bindZoomLevel();
          this.handleZoom();
        });
      }
    }

    scrollTracksToPoint(scrollValue) {
      const tracksToAdjust = document.getElementsByClassName("timeline-track-row-right");
      const tickMarksWrapper = document.getElementById("timeline-ticks-content");
      for (let track of tracksToAdjust) {
        track.scrollLeft = scrollValue;
      }
      tickMarksWrapper.scrollLeft = scrollValue;
    }

    watchForTimelineScrollChangeAndHandleIt() {
      this.zoomSliderInput.addEventListener("input", (e) => {
        const newValue = e.target.value;
        const tickMarksContainer = document.getElementById("tick-marks-container");
        const widthInPixels = tickMarksContainer.getBoundingClientRect().width;
        const newScrollLeft = widthInPixels * (newValue / 100);
        this.scrollTracksToPoint(newScrollLeft);
        this.adjustScrubberPosition();
      });
    }

    createtimelineScrubber() {
        this.timelineScrubber = document.querySelector('#timeline-hover-scrubber');
        if (!this.timelineScrubber) {
            this.timelineScrubber = document.createElement('div');
            this.timelineScrubber.id = 'timeline-hover-scrubber';
            if (this.timelineTicksContent) {
                this.timelineTicksContent.appendChild(this.timelineScrubber);
            }
        }
    }

    // Position the hover scrubber (the preview line) under the cursor. `left` is
    // a percentage of the ticks row, which spans the same width as the viewport.
    updatetimelineScrubber(e) {
        if (!this.timelineScrubber) return;

        const rect = this.timelineTicksContent.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const percent = Math.max(0, Math.min(100, (x / rect.width) * 100));

        this.timelineScrubber.style.left = `${percent}%`;
    }

    // Convert a viewport x-coordinate into a video time, accounting for the
    // current zoom (the scaled full-timeline width) and horizontal scroll.
    timeFromClientX(clientX) {
        const totalWidth = this.tickMarksContainer.getBoundingClientRect().width;
        if (!totalWidth) return 0;
        const viewport = this.timelineTicksContent.getBoundingClientRect();
        const xInContent = (clientX - viewport.left) + this.timelineTicksContent.scrollLeft;
        const ratio = Math.max(0, Math.min(1, xInContent / totalWidth));
        return ratio * this.video.duration;
    }

    seekVideoTo(time) {
        this.video.currentTime = time;
        // Also update via the player API if available
        if (window.videoPlayer && window.videoPlayer.skipTo) {
            window.videoPlayer.skipTo(time);
        }
    }

    // Whether an event target is somewhere a click should seek: inside a seek
    // region but not on an annotation's content (which selects the annotation).
    isSeekTarget(target) {
        if (!target.closest(SEEK_REGION_SELECTOR)) return false;
        if (target.closest('.track-item-content')) return false;
        return true;
    }

    startTimelineDrag(e) {
        this.isDragging = true;

        // Pause during the scrub, remembering whether to resume afterwards.
        this.wasPlayingBeforeDrag = !this.video.paused;
        if (this.wasPlayingBeforeDrag) {
            this.video.pause();
        }

        // Hide hover scrubber during drag
        if (this.timelineScrubber) {
            this.timelineScrubber.style.opacity = '0';
        }

        // Seek to initial position
        this.seekVideoTo(this.timeFromClientX(e.clientX));

        e.preventDefault();
    }

    endTimelineDrag() {
        this.isDragging = false;

        // Resume playback if it was playing before
        if (this.wasPlayingBeforeDrag) {
            this.video.play();
        }

        this.wasPlayingBeforeDrag = false;
    }

    attachTimelineListeners() {
        if (!this.timelineWrapper) return;

        // Track the cursor and show the hover scrubber across the seek regions.
        this.timelineWrapper.addEventListener('mousemove', (e) => {
            if (this.isDragging) return;
            if (!this.isSeekTarget(e.target)) {
                if (this.timelineScrubber) this.timelineScrubber.style.opacity = '0';
                return;
            }
            this.updatetimelineScrubber(e);
            if (this.timelineScrubber) this.timelineScrubber.style.opacity = '1';
        });

        this.timelineWrapper.addEventListener('mouseleave', () => {
            if (this.timelineScrubber && !this.isDragging) {
                this.timelineScrubber.style.opacity = '0';
            }
        });

        // Begin scrubbing on a left mousedown inside a seek region. Resize handles
        // stop propagation before this fires, so they still resize rather than seek.
        this.timelineWrapper.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            if (!this.isSeekTarget(e.target)) return;
            this.startTimelineDrag(e);
        });

        document.addEventListener('mousemove', (e) => {
            if (this.isDragging) {
                this.seekVideoTo(this.timeFromClientX(e.clientX));
                e.preventDefault();
            }
        });

        document.addEventListener('mouseup', () => {
            if (this.isDragging) {
                this.endTimelineDrag();
            }
        });
    }

    attachVideoListeners() {
        // timeupdate fires only a few times a second, so it drives the periodic
        // geometry refresh and keeps the scrubber correct while paused/seeking.
        this.video.addEventListener('timeupdate', () => this.adjustScrubberPosition());
        this.video.addEventListener('seeked', () => this.adjustScrubberPosition());
        window.addEventListener('resize', () => this.adjustScrubberPosition());
        this.adjustScrubberPosition();
        // During playback, interpolate every animation frame for smooth motion
        // (shared mechanism with AnnotationPlayer's progress scrubber).
        animateDuringPlayback(this.video, () => this.paintScrubber());
    }

    watchAndHandleAnnotationSetMenuOpen() {
      const annotationSetMenuWrapper = document.getElementById("annotation-panel-header");
      this.watchForMultiSelectMenuOpen(annotationSetMenuWrapper);
    }

    async handleAnnotationSetCreation(setName, annotationSetId = undefined, annotationSetJson = undefined) {
      const createResponse = await fetch("/annotation-set/create", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          content_id: this.contentId,
          name: setName,
          annotation_set_id_to_copy: annotationSetId,
          annotation_set_json: annotationSetJson
        })
      });

      if (!createResponse.ok) {
        console.error("Failed to create new annotation set");
        return;
      }
      window.location.reload();
    }

    toggleAnnotationSetOptionSelectorAndContent() {
      // hide the options selector and show the selected option content
      // or do the opposite if the options selector should be shown
      const selectOptionContent = document.getElementById("annotation-set-modal-base-content");
      const optionContentContainer = document.getElementById("annotation-set-modal-option-display");
      if (selectOptionContent.className.includes("hidden")) {
        optionContentContainer.classList.add("hidden");
        selectOptionContent.classList.remove("hidden");
      } else {
        selectOptionContent.classList.add("hidden");
        optionContentContainer.classList.remove("hidden");
      }
    }

    async setupAndDisplayAnnotationSetOption(url) {
      const annotationSetOptionsModalContent = document.getElementById("annotation-set-modal-option-display");
      const contentResponse = await fetch(url);
      if (!contentResponse.ok) {
        console.error("Failed to get new content for annotation set options modal");
        return false;
      }
      const newHTML = await contentResponse.text();
      annotationSetOptionsModalContent.innerHTML = newHTML;

      // set up back button
      const backButton = document.getElementById("annotation-set-modal-back");
      backButton.addEventListener("click", () => {
        this.toggleAnnotationSetOptionSelectorAndContent();
      });

      this.toggleAnnotationSetOptionSelectorAndContent();
      return true;
    }

    async setupAnnotationSetUseExistingModal() {
      const result = await this.setupAndDisplayAnnotationSetOption(`/annotation-options-modal/use-existing/${this.contentId}`);
      if (!result) {
        return;
      }
      const confirmButton = document.getElementById("annotation-set-use-existing-button");
      confirmButton.addEventListener("click", async (event) => {
        const parent = event.target.closest("#annotation-set-use-existing-option");
        const setSelector = parent.querySelector(".annotation-set-selector");
        if (setSelector.value == undefined || setSelector.value == "") {
          setSelector.classList.add("invalid-input");
          return;
        } else {
          setSelector.classList.remove("invalid-input");
        }
        const contentSetAssignmentResponse = await fetch("/select-annotation-set", {
          method: "POST",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            "content_id": this.contentId,
            "annotation_set_id": setSelector.value
          })
        });
        if (!contentSetAssignmentResponse.ok) {
          console.error("Failed to set annotation set for this content");
          return;
        }
        window.location.reload();
      });
    }

    async setupAnnotationSetCopyModal() {
      const result = await this.setupAndDisplayAnnotationSetOption(`/annotation-options-modal/copy-from-set/${this.contentId}`);
      if (!result) {
        return;
      }
      const createButton = document.getElementById("annotation-set-copy-from-button");
      createButton.addEventListener("click", (event) => {
        const parent = event.target.closest("#annotation-set-modal-copy-from-set");
        const nameInput = parent.querySelector("#copy-from-annotation-set-name");
        let isInvalid = false;
        if (!nameInput.value) {
          isInvalid = true;
          nameInput.classList.add("invalid-input");
        } else {
          nameInput.classList.remove("invalid-input");
        }
        const setSelection = parent.querySelector(".annotation-set-selector");
        if (setSelection.value == undefined || setSelection.value == '') {
          isInvalid = true;
          setSelection.classList.add("invalid-input");
        } else {
          setSelection.classList.remove("invalid-input");
        }
        if (isInvalid) return;
        this.handleAnnotationSetCreation(nameInput.value, setSelection.value);
      });
    }

    async setupAnnotationSetImportModal() {
      const result = await this.setupAndDisplayAnnotationSetOption("/annotation-options-modal/import");
      if (!result) {
        return;
      }
      const createButton = document.getElementById("create-annotation-set-from-import-button");
      createButton.addEventListener("click", async (event) => {
        const parent = event.target.closest("#import-create-annotation-set");
        let isInvalid = false;
        const nameInput = parent.querySelector("#new-annotation-set-name");
        if (!nameInput.value) {
          isInvalid = true;
          nameInput.classList.add("invalid-input");
        } else {
          nameInput.classList.remove("invalid-input");
        }
        const fileInput = parent.querySelector("#annotation-set-import-file-input");
        if (fileInput.files.length <= 0) {
          isInvalid = true;
          fileInput.classList.add("invalid-input");
        } else {
          fileInput.classList.remove("invalid-input");
        }
        if (isInvalid) return;
        const jsonFile = await fileInput.files[0].text();
        try {
          JSON.parse(jsonFile);
        } catch {
          console.error("Invalid JSON provided");
          return;
        }

        this.handleAnnotationSetCreation(nameInput.value, undefined, jsonFile);
      });
    }

    async setupAnnotationSetCreationModal() {
      const result = await this.setupAndDisplayAnnotationSetOption("/annotation-options-modal/create");
      if (!result) {
        return;
      }
      const createAnnotationSetButton = document.getElementById("annotation-set-create-button");
      createAnnotationSetButton.addEventListener("click", (event) => {
        const parent = event.target.closest("#annotation-set-create-new-content");
        const setName = parent.querySelector("#create-annotation-set-name");
        if (!setName.value) {
          setName.classList.add("invalid-input");
          return;
        } else {
          setName.classList.remove("invalid-input");
        }
        this.handleAnnotationSetCreation(setName.value);
      });
    }

    setupAnnotationSetOptionsModal() {
      const viewExistingButton = document.getElementById("annotation-set-view-existing");
      if (viewExistingButton) {
        viewExistingButton.addEventListener("click", this.setupAnnotationSetUseExistingModal.bind(this));
      }

      const viewCopyButton = document.getElementById("annotation-set-view-copy");
      if (viewCopyButton) {
        viewCopyButton.addEventListener("click", this.setupAnnotationSetCopyModal.bind(this));
      }

      const viewImportButton = document.getElementById("annotation-set-view-import");
      viewImportButton.addEventListener("click", this.setupAnnotationSetImportModal.bind(this));

      const viewCreateButton = document.getElementById("annotation-set-view-create");
      viewCreateButton.addEventListener("click", this.setupAnnotationSetCreationModal.bind(this));
    }



    watchAndHandleAnnotationSetDelete() {
      const deleteAnnotationSetButton = document.getElementById("annotation-set-delete");
      if (!deleteAnnotationSetButton) {
        console.error("Failed to get annotation set delete button");
        return;
      }

      deleteAnnotationSetButton.addEventListener("click", async () => {
        const annotationSetId = this.timelineWrapper.dataset["annotationSetId"];
        if (isNaN(annotationSetId) || annotationSetId === undefined || annotationSetId == "") {
          return;
        }
        const deleteResponse = await fetch(`/annotation-set/delete/${annotationSetId}`, {
          method: "DELETE",
          headers: {
            "X-CSRFToken": getCSRFToken()
          }
        });
        if (!deleteResponse.ok) {
          console.error("Failed to delete annotation set");
          return;
        }

        window.location.reload();
      });
    }

    watchAndHandleAnnotationSetExport() {
      const exportAnnotationSetModal = document.getElementById("export-annotation-set");
      const exportButton = document.getElementById("annotation-set-export-button");
      exportButton.addEventListener("click", async () => {
        const exportLink = document.getElementById("export-annotation-set-link");
        const setSelector = exportAnnotationSetModal.querySelector(".annotation-set-selector");
        const annotationSetId = setSelector.value;
        if (isNaN(Number(annotationSetId)) || annotationSetId == '' || annotationSetId == undefined) {
          // prevent any non-number value from being injected into link
          return;
        }
        exportLink.href = `/annotation-set/export/${Number(annotationSetId)}`;
        exportLink.click();
      });
    }

    async handleAnnotationSetChange(event) {
        event.stopPropagation();
        let annotationSetId = event.target.value;

        if (isNaN(annotationSetId) || annotationSetId === undefined) {
            console.error("Selected value was not defined!");
            return;
        }

        if (!this.contentId) {
            console.error("could not retrieve content id while switching annotation sets!");
            return;
        }
        const htmlContentResponse = await fetch("/select-annotation-set", {
            method: "POST",
            body: JSON.stringify({"annotation_set_id": annotationSetId, "content_id": this.contentId}),
            headers: {
              "X-CSRFToken": getCSRFToken(),
              "Content-Type": "application/json"
            },
            mode: "same-origin"
        });
        if (!htmlContentResponse.ok) {
          console.error("Failed to update annotation_set!");
          return;
        }
        // rebuild the page with the new annotation set
        window.location.reload();
    }

    watchForAnnotationSetNameChangeAndHandleIt() {
      const annotationSetSettingsEl = document.getElementById("annotation-set-settings-compact");
      const annotationSetId = annotationSetSettingsEl.dataset["annotationSetId"];
      const annotationNameInput = document.getElementById("annotation-set-name");
      const annotationNameSubmitButton = document.getElementById("annotation-name-submit-button");

      const handleNameChange = async () => {
        const currentAnnotationSetName = annotationSetSettingsEl.dataset["annotationSetName"];
        const newName = annotationNameInput.value.trim();
        const nameChangeResponse = await fetch("/annotation-set/update-name/", {
            method: "POST",
            headers: {
              "X-CSRFToken": getCSRFToken(),
              "Content-Type": "application/json"
            },
            body: JSON.stringify({
              annotation_set_id: annotationSetId,
              name: newName
            })
        });
        if (!nameChangeResponse.ok) {
          console.error("Failed to update annotation set name");
          annotationNameInput.value = currentAnnotationSetName;
          return;
        }
        annotationNameInput.value = newName;
        annotationSetSettingsEl.dataset["annotationSetName"] = newName;
        const annotationSetOptionName = annotationSetSettingsEl.querySelector(`.annotation-set-option[value="${annotationSetId}"] .set-option-name`);
        annotationSetOptionName.innerText = newName;
      }

      annotationNameInput.addEventListener("keydown", (e) => {
        if (e.key == "Enter") {
          handleNameChange();
        }
      })
      annotationNameSubmitButton.addEventListener("click", handleNameChange);
    }

    handleNoAnnotationSet() {
      // This is designed to run only when the editor loads to encourage a user to select,
      // create, or import an annotation set
      const annotationSetId = this.timelineWrapper.dataset["annotationSetId"];
      if (annotationSetId === '' || annotationSetId === undefined) {
        // prevent user from leaving modal if they need to pick an annotation set
        const optionsModal = document.getElementById("annotation-set-options-modal");
        const exitButton = optionsModal.querySelector(".close-dialog-button");
        exitButton.remove();

        // open the annotation set options modal
        const annotationSetOptionsButton = document.getElementById("open-annotation-set-options-modal");
        annotationSetOptionsButton.click();
      }
    }

    async handleRemoveEditor(e) {
      e.stopPropagation();
      const annotationSetSettingsEl = document.getElementById("annotation-set-settings-compact");
      const annotationSetId = annotationSetSettingsEl.dataset["annotationSetId"];
      // because the remove button has an element inside it, e.target could refer to the image element,
      // or the button element which is the img's parent. The editorId is only on the button element.
      // To get around this, the closest .remove-editor-button will find the correct in either case.
      const buttonEl = e.target.closest(".remove-editor-button");
      const editorId = buttonEl.dataset["editorId"];
      const removalResponse = await fetch(`/annotation-set/${annotationSetId}/remove-editor/${editorId}/`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCSRFToken()
        }
      });
      if (!removalResponse.ok) {
        console.error("Failed to remove editor");
        return;
      }
      e.target.closest(".annotation-set-editor-details").remove();
    }

    attachRemoveEditorListener(editorDetailEl) {
      const removeEditorButton = editorDetailEl.querySelector(".remove-editor-button");
      if (removeEditorButton) {
        removeEditorButton.addEventListener("click", this.handleRemoveEditor.bind(this));
      }
    }

    attachRemoveEditorListeners() {
      const editorDetailEls = document.getElementsByClassName("annotation-set-editor-details");
      for (let editorDetailEl of editorDetailEls) {
        this.attachRemoveEditorListener(editorDetailEl);
      }
    }

    async handleAddEditor(e) {
      const annotationSetSettingsEl = document.getElementById("annotation-set-settings-compact");
      const annotationSetId = annotationSetSettingsEl.dataset["annotationSetId"];
      const editorId = e.target.dataset["editorId"];
      const selectedEditorsResponse = await fetch("/annotation-set/add-editor", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          annotation_set_id: annotationSetId,
          editor_id: editorId
        })
      });
      if (!selectedEditorsResponse.ok) {
        console.error("Failed to add new editor");
        return;
      }
      const newWrapper = await selectedEditorsResponse.text();
      const selectedEditorsWrapper = document.getElementById("selected-editors");
      selectedEditorsWrapper.outerHTML = newWrapper;
      this.attachRemoveEditorListeners();
    }

    attachAddEditorListeners() {
      const editorSearchResultsWrapper = document.getElementById("editor-search-results");
      const resultEntries = editorSearchResultsWrapper.querySelectorAll(".editor-search-result");
      if (resultEntries.length == 0) {
        editorSearchResultsWrapper.innerText = "No search results";
        return;
      }
      for (let resultEl of resultEntries) {
        resultEl.addEventListener("click", this.handleAddEditor.bind(this));
      }
    }

    watchForEditorSearchInputAndHandleIt() {
      /* Watch for keydown on input field, if its been less than timer amount since
      the last keydown, clear the last request and start the timer again. If
      the timer ends, execute the search function. */
      const editorSearchInput = document.getElementById("editor-search-input");
      let keydownTimerId;
      const handleSearchInput = () => {
        clearTimeout(keydownTimerId);
        keydownTimerId = setTimeout(async () => {
          const searchResultsWrapper = document.getElementById("editor-search-results");
          const searchString = editorSearchInput.value.trim();
          if (searchString == "") {
            searchResultsWrapper.innerHTML = "";
          }
          if (!searchString) {
            return;
          }

          // remove old search results
          const oldSearchResultEls = document.getElementsByClassName("editor-option");
          for (let i = oldSearchResultEls.length - 1; i >= 0; i--) {
            const resultEl = oldSearchResultEls[i];
            resultEl.remove();
          }

          // execute request
          const searchResponse = await fetch("/annotation-set/search-for-editor", {
            method: "POST",
            headers: {
              "X-CSRFToken": getCSRFToken(),
              "Content-Type": "application/json"
            },
            body: JSON.stringify({search_string: searchString})
          });

          if (!searchResponse.ok) {
            console.error("Failed to execute editor search");
            searchResultsWrapper.innerText = "An error has occurred, please try searching again.";
            return;
          }
          const results = await searchResponse.text();
          searchResultsWrapper.outerHTML = results;
          this.attachAddEditorListeners();
        }, 300);
      }
      editorSearchInput.addEventListener("keydown", handleSearchInput.bind(this));
    }

    watchAndHandleEditorPanelSwitch() {
      const annotationPanel = document.getElementById("editor-annotation-panel");
      const subtitlesPanel = document.getElementById("subtitle-editor-panel");
      const togglePanelVisiblity = () => {
        annotationPanel.classList.toggle("editor-annotation-panel-hidden");
        annotationPanel.classList.toggle("editor-annotation-panel-visible");
        subtitlesPanel.classList.toggle("subtitle-editor-panel-visible");
        subtitlesPanel.classList.toggle("subtitle-editor-panel-hidden");
      }
      const annotationPanelSwitchButton = document.getElementById("annotation-panel-switch");
      const subtitlePanelSwitchButton = document.getElementById("subtitle-panel-switch");
      annotationPanelSwitchButton.addEventListener("click", togglePanelVisiblity);
      subtitlePanelSwitchButton.addEventListener("click", togglePanelVisiblity);
    }

    watchAndHandleSubtitleTrackChange() {
      const subtitleSelectInput = document.getElementById("subtitles-track-selector");
      subtitleSelectInput.addEventListener("change", async () => {
        const newSubtitleTrackId = subtitleSelectInput.value;
        if (subtitleSelectInput == undefined) {
          console.error("Invalid subtitle track id");
          return;
        }
        this.selectedSubtitleTrackId = newSubtitleTrackId;
        const subtitlesResponse = await fetch(`/subtitles/get-editable-subtitles/${this.selectedSubtitleTrackId}`);
        if (!subtitlesResponse.ok) {
          console.error("Failed to get subtitle cues");
          return;
        }
        const subtitlesPanelHTML = await subtitlesResponse.text();
        const currentSubtitlesPanel = document.getElementById("subtitle-panel-content-wrapper");
        currentSubtitlesPanel.outerHTML = subtitlesPanelHTML;
        const subtitlesSettingsModal = document.getElementById("subtitles-settings");
        this.buildWatchersForSubtitlePanelContent();
        this.buildWatchersForSubtitleEditorCues();
        subtitlesSettingsModal.close();
      })
    }

    collectCues() {
      const rawCues = document.getElementsByClassName("editor-subtitle-cue");
      const cues = [];
      for (let cue of rawCues) {
        // gather elements to extract data from
        const typeEl = cue.querySelector(".editor-subtitle-cue-type");
        const payloadEl = cue.querySelector(".editor-subtitle-cue-content");
        const identifierEl = cue.querySelector(".editor-subtitle-cue-identifier");
        const startTimeEl = cue.querySelector(".editor-subtitle-cue-start");
        const endTimeEl = cue.querySelector(".editor-subtitle-cue-end");
        const settingsEl = cue.querySelector(".editor-subtitle-cue-settings");

        // extract the data and package into array to send to backend
        cues.push({
          type: typeEl.value,
          payload: payloadEl.value,
          identifier: identifierEl.value,
          start_time: startTimeEl.value,
          end_time: endTimeEl.value,
          cue_settings: settingsEl.value,
        });
      }
      return cues;
    }

    async saveCues(cues, isAutosave=true) {
      if (typeof isAutosave !== "boolean") {
        console.error("isAutosave must be a boolean");
        return;
      }

      const updateResponse = await fetch("/subtitles/update-subtitle-cues", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          subtitle_id: this.selectedSubtitleTrackId,
          cues: cues,
          seconds_nudge: 0,
          nudge_excluded_cues: [],
          is_autosave: isAutosave
        })
      });

      if (!updateResponse.ok) {
        console.error("Failed to save cues");
        return;
      }

      const subtitleCueListWrapper = document.getElementById("subtitle-panel-list");
      subtitleCueListWrapper.innerHTML = await updateResponse.text();
      this.buildWatchersForSubtitleEditorCues();
    }

    buildWatchersForSubtitlePanelContent() {
      const subtitlesPanel = document.getElementById("subtitle-panel-content-wrapper");
      const addNewCueButton = subtitlesPanel.querySelector("#add-new-subtitle-button");
      addNewCueButton.addEventListener("click", () => {
        const cues = this.collectCues();
        // build new cue and append it to cues array
        const time = this.video.currentTime;
        // the back end sorts cues, so this will go in its correct place.
        cues.push(
          {
            start_time: formatSecondsToString(time, true),
            end_time: formatSecondsToString(time + 2, true),
            type: "CUE",
            payload: "",
            identifier: "",
            cue_settings: "",
          }
        )
        this.saveCues(cues, false)
      });
    }

    buildWatchersForSubtitleEditorCues() {
      const subtitlesPanel = document.getElementById("subtitle-panel-content-wrapper");
      const deleteCue = (e) => {
        const editorSubtitleCueEl = e.target.closest('.editor-subtitle-cue');
        editorSubtitleCueEl.remove();
        this.saveCues(this.collectCues(), false);
      }
      const editorSubtitleCueDeleteButtons = subtitlesPanel.querySelectorAll(".editor-subtitle-cue-delete");
      for (let cueDelButton of editorSubtitleCueDeleteButtons) {
        cueDelButton.addEventListener("click", deleteCue.bind(this));
      }

      const saveUpdatedInformation = () => {
        this.saveCues(this.collectCues(), false);
      }
      const cueInputs = subtitlesPanel.querySelectorAll(".editor-subtitle-cue-start, .editor-subtitle-cue-end, .editor-subtitle-cue-content");
      for (let cueInput of cueInputs) {
        cueInput.addEventListener("keydown", (e) => {
          if (e.key != "Enter") {
            return;
          }
          saveUpdatedInformation();
        });
      }
    }
}

function editorInit() {
  // Initialize when DOM is ready
  let editor;
  if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
          editor = new Editor();
      });
  } else {
      editor = new Editor();
  }
  editor.setUpAnnotationPanelClickListeners();
}

const checkVideo = setInterval(() => {
    const video = document.querySelector('.annotation-player-container video');
    if (video && !isNaN(video.duration) && window?.videoPlayer) {
      clearInterval(checkVideo);
      editorInit();
    }
}, 100);
