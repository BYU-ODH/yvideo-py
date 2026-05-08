import { formatSecondsToString } from "./utils.js";

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
        this.timelineTicks = document.querySelector('.timeline-ticks');
        this.timelineTicksContent = document.querySelector('.timeline-ticks-content');
        this.timelineWrapper = document.getElementById('timeline-wrapper');
        this.zoomSliderInput = document.getElementById('timeline-scroll-input');
        this.editorScrubber = document.querySelector('#editor-scrubber');
        this.zoomLevel = 1;
        this.timelineScrubber = null;
        this.isDragging = false;
        this.wasPlayingBeforeDrag = false;
        this.annotationUpdatedEvent = new CustomEvent("annotationUpdated");
        this.selectedSubtitleTrackId = null;

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
        this.watchForItemFormChanges();

        this.renderTickMarksAndLabels();
        this.attachZoomListener();
        this.createtimelineScrubber();
        this.attachTimelineListeners();
        this.attachVideoListeners();
        this.setupTrackWatchersForAllTracks();
        this.watchForTrackCreation();
        this.watchForClickOutsideOfTrackMenu();
        this.watchForTimelineScrollChangeAndHandleIt();
        this.setupAnnotationSelectorFunctions();
        this.watchForAnnotationSetNameChangeAndHandleIt();
        this.attachRemoveEditorListeners();
        this.watchForEditorSearchInputAndHandleIt();
        this.watchAndHandleEditorPanelSwitch();
        this.watchAndHandleSubtitleTrackChange();
    }

    getCSRFToken() {
      return document.querySelector('[name=csrfmiddlewaretoken]').value;
    }

    updateTracks() {
      const tracks = document.querySelectorAll('.track-row-annotations-container');
      tracks.forEach(container => {
          container.addEventListener('mousedown', this.handleMouseDown.bind(this));
      });
      this.tracks = tracks;
    }

    handleMouseDown(e) {
        const trackItem = e.target.closest('.track-item');
        if (!trackItem) return;

        // Always trigger the form load first, regardless of where clicked
        const contentArea = trackItem.querySelector('.track-item-content');
        if (contentArea) {
            // Use htmx to trigger the GET request to load the form if available,
            // otherwise dispatch a DOM event so other code can listen for it.
            if (window.htmx && typeof window.htmx.trigger === 'function') {
                window.htmx.trigger(contentArea, 'track-item-click');
            } else {
                const evt = new CustomEvent('track-item-click', { bubbles: true, cancelable: true });
                contentArea.dispatchEvent(evt);
            }
        }

        const resizeHandle = e.target.closest('.resize-handle');

        if (resizeHandle) {
            this.startResize(trackItem, resizeHandle, e);
            e.preventDefault();
            e.stopPropagation();
        } else if (!e.target.closest('.resize-handle')) {
            this.startDrag(trackItem, e);
            e.preventDefault();
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

    startDrag(trackItem, e) {
        const itemContainer = trackItem.closest('.track-row-annotations-container');
        const rect = itemContainer.getBoundingClientRect();
        const containerWidth = itemContainer.scrollWidth || rect.width;
        const itemLeft = this.calculateItemLeftAsDecimal(trackItem);
        const itemWidth = this.calculateItemWidthAsDecimal(trackItem);

        this.dragState = {
            type: 'drag',
            item: trackItem,
            container: itemContainer,
            startX: e.clientX,
            startLeft: itemLeft * 100,
            containerWidth,
            hasMoved: false,
            originalLeft: itemLeft,
            originalWidth: itemWidth
        };

        // Reset deltas
        trackItem.dataset.deltaLeft = '0';
        trackItem.dataset.deltaWidth = '0';

        trackItem.classList.add('dragging');
        document.body.classList.add('dragging', 'dragging-item');
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
            if (this.dragState.type === 'drag') {
                this.updateDragPosition(e);
            } else if (this.dragState.type === 'resize') {
                this.updateResizePosition(e);
            }
        }

        e.preventDefault();
    }

    updateDragPosition(e) {
        const deltaX = e.clientX - this.dragState.startX;
        // Don't account for zoom in percent calculation - container width is already adjusted
        const deltaPercent = (deltaX / this.dragState.containerWidth) * 100;
        let newLeft = this.dragState.startLeft + deltaPercent;

        // Special handling for pause items (width is fixed, only left moves)
        if (this.dragState.item.dataset.itemType === "pause") {
            const minWidthPercent = 0.5;
            newLeft = Math.max(0, Math.min(newLeft, 100 - minWidthPercent));

            this.dragState.item.style.left = `${newLeft}%`;

            const deltaLeft = newLeft - this.dragState.originalLeft;
            this.dragState.item.dataset.deltaLeft = deltaLeft.toFixed(2);

            this.seekToHandlePosition(true, newLeft, minWidthPercent);
        } else {
            let width = parseFloat(this.dragState.item.style.width);
            if (width === '' || width === undefined || isNaN(width)) {
              const itemRect = this.dragState.item.getBoundingClientRect();
              width = itemRect.width / this.dragState.containerWidth * 100;
              this.dragState.item.style.width = `${width}%`;
            }
            newLeft = Math.max(0, Math.min(newLeft, 100 - width));

            this.dragState.item.style.left = `${newLeft}%`;

            const deltaFromOriginal = newLeft - this.dragState.originalLeft;
            this.dragState.item.dataset.deltaLeft = deltaFromOriginal.toFixed(2);

            const video = document.querySelector('.annotation-player-container video');
            if (video) {
                const targetTime = (newLeft / 100) * this.duration;
                video.currentTime = targetTime;

                if (window.videoPlayer && window.videoPlayer.skipTo) {
                    window.videoPlayer.skipTo(targetTime);
                }
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

        state.item.classList.remove('dragging');
        document.body.classList.remove('dragging', 'dragging-item', 'resizing', 'resizing-item');

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

    listenForItemUpdateFormSubmission() {
      const itemForm = document.getElementById("annotation-update-form");
      if (!itemForm) {
        return;
      }
      const annotationId = itemForm.dataset["annotationId"];
      const annotationType = itemForm.dataset["annotationType"];
      itemForm.addEventListener("submit", (e) => {
        e.preventDefault();
        this.updateAnnotation({annotationType, annotationId})
      })
    }

    async deleteItem(annotationType, annotationId) {
      const response = await fetch(`/annotations/${annotationType}/${annotationId}/delete`, {
        method: "delete",
        headers: {"X-CSRFToken": this.getCSRFToken()}
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

    placeNewCensorPositionHtml(censor_parent_id, html) {
      const annotationUpdateForm = document.getElementById("existing-item-form");
      const currentFormId = annotationUpdateForm.dataset["annotationId"];
      const censorPositionWrapperEl = document.getElementById("censor-positions-wrapper");
      // don't do anything if the user has moved onto a different item
      if (!censorPositionWrapperEl || censor_parent_id != currentFormId) {
        return;
      }
      censorPositionWrapperEl.outerHTML = html;
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
        headers: {"X-CSRFToken": this.getCSRFToken(), "Content-Type": "application/json"},
        body: JSON.stringify({parent_annotation_id: parentCensorId, time, x, y, width, height})
      });
      if (response.status == 201) {
        const responseHtml = await response.text();
        this.placeNewCensorPositionHtml(parentCensorId, responseHtml)
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
          "X-CSRFToken": this.getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({position_id: positionId, time, x, y, width, height})
      });
      if (response.status == 201) {
        const responseHtml = await response.text();
        this.placeNewCensorPositionHtml(parentAnnotationId, responseHtml)
        window.dispatchEvent(this.annotationUpdatedEvent);
      }
      else {
        console.error("Failed to update censor position");
      }
    }

    async deleteCensorPosition(parentAnnotationId, positionId) {
      const response = await fetch(`/annotations/censor-position/delete/${positionId}`, {
        method: "DELETE",
        headers: {"X-CSRFToken": this.getCSRFToken()}
      });
      if (response.status == 200) {
        const responseHtml = await response.text();
        this.placeNewCensorPositionHtml(parentAnnotationId, responseHtml);
        window.dispatchEvent(this.annotationUpdatedEvent);
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

            point.addEventListener("pointermove", onMove);
            point.addEventListener("pointerup", onPointerUp.bind(this));
            point.addEventListener("pointercancel", onCancel.bind(this));
            document.addEventListener("keyup", handleEscKeyPress.bind(this));
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

    handleItemFormChanges(mutationList) {
      for (let mutation of mutationList) {
        if (mutation.type == "childList") {
          this.listenForItemUpdateFormSubmission();
          this.setUpItemFormDeleteButton();
          this.changeAnnotationInFocus();
        }
      }
    }

    watchForItemFormChanges() {
      const itemFormObserver = new MutationObserver(this.handleItemFormChanges.bind(this))
      const itemForm = document.getElementById("detail-form");
      itemFormObserver.observe(itemForm, { childList: true });
    }

    async updateAnnotation({annotationType, annotationId, name=undefined, description=undefined, startTime=undefined, endTime=undefined, isFromItem=false, autoUpdateItem=true, autoUpdateForm=true}) {

      let requestBody, contentType;
      if (isFromItem) {
        requestBody = JSON.stringify({
          "content_id": this.contentId,
          "name": name,
          "description": description,
          "start_time": startTime,
          "end_time": endTime,
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
          "X-CSRFToken": this.getCSRFToken(),
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
        // You must get the new element before making the style changes that occur while placing the track item.
        const newTargetItem = document.getElementById(`${annotationType}-${annotationId}`);
        this.placeTrackItems();
        this.setUpItemClickListeners(newTargetItem);
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
        }
        this.video.currentTime = time;
        this.markCensorPositionAsActive(parent.dataset["positionId"]);
      }
      const buttons = document.getElementsByClassName("censor-position-seek-button");
      for (let button of buttons) {
        button.addEventListener("click", handler);
      }
    }

    setUpCensorPositionDeleteListeners(parentAnnotationId) {
      const buttons = document.getElementsByClassName("censor-position-delete-button");
      for (let button of buttons) {
        const buttonParent = button.parentElement;
        const positionId = buttonParent.dataset["positionId"];
        async function deleteCensor() {
          await this.deleteCensorPosition(parentAnnotationId, positionId)
        }
        button.addEventListener("click", deleteCensor.bind(this))
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
      if (annotationType == "censor") {
        this.setUpCensorPositionDeleteListeners(annotationId);
        this.setupCensorPositionSeekListeners();
      }
      else if (annotationType == "comment") {
        this.setUpCommentChangeListeners(detailForm);
        this.presentCommentBoxPositionAndSizeControls(annotationId);
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
            // allow propagation only if the patent item is not active
            const parentItem = element.closest(".track-item");
            if (parentItem.className.includes("active-track-item")) {
              e.stopPropagation();
              this.video.currentTime = parseFloat(positionLocator.dataset["positionTime"]);
              this.markCensorPositionAsActive(positionLocator.dataset["positionId"]);
              return;
            }
            // Wait a moment, to allow html to be loaded into the DOM
            setTimeout(() => {
              this.video.currentTime = parseFloat(positionLocator.dataset["positionTime"]);
              this.markCensorPositionAsActive(positionLocator.dataset["positionId"]);
            }, 50);
          })
        }
      }
    }

    setUpClickListenersForAllPanelAndTrackItems() {
      const trackItems = document.getElementsByClassName("track-item");
      for (let trackItem of trackItems) {
        this.setUpItemClickListeners(trackItem);
      }

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
    setupTrackWatchersForAllTracks() {
      const trackRows = document.getElementsByClassName("track-row");
      for (let trackRow of trackRows) {
        this.setupTrackWatchers(trackRow);
      }
    }

    // use this method to apply all relevant watchers (event listeners) to
    // tracks that are new to the DOM.
    setupTrackWatchers(trackRootElement) {
      this.watchForTrackMenuOpen(trackRootElement);
      this.watchForDisplayTrackRename(trackRootElement);
      this.watchForTrackRename(trackRootElement);
      this.watchForTrackMovement(trackRootElement);
      this.watchForTrackDelete(trackRootElement);
    }

    /* track options menu */
    handleTrackOpenMenuClick(e) {
      e.stopPropagation();
      // we don't want more than one track menu visible at one time
      const visibleMenuCSSClass = "visible-timeline-track-menu";
      const allVisibleTrackMenus = document.getElementsByClassName(visibleMenuCSSClass);
      for (let menu of allVisibleTrackMenus) {
        menu.classList.remove(visibleMenuCSSClass);
      }

      // get track menu and position it properly
      const wrapperDim = this.timelineWrapper.getBoundingClientRect();
      const trackMenuWrapper = e.target.closest(".timeline-track-edit-wrapper");
      const trackOptionsMenu = trackMenuWrapper.querySelector(".timeline-track-menu");
      trackOptionsMenu.style.visibility = "hidden";
      trackOptionsMenu.classList.add(visibleMenuCSSClass);
      const trackMenuDim = trackOptionsMenu.getBoundingClientRect();
      if ((trackMenuDim.bottom - wrapperDim.bottom) > -20) {
        trackOptionsMenu.classList.add("track-menu-bumped-up");
      }

      // now we are safe to make the track menu visible
      trackOptionsMenu.style.visibility = "";
    }

    watchForTrackMenuOpen(trackRootElement) {
      const menuButton = trackRootElement.querySelector(".editor-menu-button");
      if (menuButton) {
        menuButton.addEventListener("click", this.handleTrackOpenMenuClick.bind(this));
      }
      else {
        console.error("No menu button found for track");
      }
    }

    watchForClickOutsideOfTrackMenu() {
      const editorContainer = document.getElementById("editor-container");
      editorContainer.addEventListener("click", (e) => {
        const visibleTrackMenus = document.getElementsByClassName("visible-timeline-track-menu");
        for (let menu of visibleTrackMenus) {
          const menuDim = menu.getBoundingClientRect();
          if (e.x < menuDim.left || e.x > menuDim.right || e.y < menuDim.top || e.y > menuDim.bottom) {
            menu.classList.remove("visible-timeline-track-menu");
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
        const trackTemplate = document.createElement("template");
        trackTemplate.innerHTML = newTrackHTML;
        const trackParentNode = trackTemplate.content.childNodes[0];
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
          headers: {"X-CSRFToken": this.getCSRFToken()},
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
          "X-CSRFToken": this.getCSRFToken(),
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
            "X-CSRFToken": this.getCSRFToken(),
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
            "X-CSRFToken": this.getCSRFToken(),
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
      this.setUpClickListenersForAllPanelAndTrackItems();
      this.setUpItemElevationOnClick();
    }

    setUpItemElevationOnClick() {
      const itemsToSetUp = document.querySelectorAll(".track-item[data-setup='false']");
      for (let item of itemsToSetUp) {
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
                  "X-CSRFToken": this.getCSRFToken(),
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
              const newElement = document.createElement("template");
              newElement.innerHTML = newTrackItemHtml;
              const newNode = newElement.content.firstChild;
              newNode.dataset["start"] = startTime;
              newNode.dataset["end"] = endTime;
              trackContainer.appendChild(newNode);
              this.setUpItemClickListeners(newNode);
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

    adjustScrubberPosition() {
      /* The scrubber is bound between 0% and 100%, it never goes off the page,
     so we need to position it based on the current time of the video and how far zoomed
    the page timeline is. We can think of the currently displayed time as a window on slider.*/
      const totalTimeline = document.getElementById("tick-marks-container");
      const totalScrollableWidth = totalTimeline.getBoundingClientRect().width;
      const visibleTimeline = document.getElementById("timeline-ticks-content");
      const timeWindowWidth = visibleTimeline.getBoundingClientRect().width;
      const currentPositionLeft = visibleTimeline.scrollLeft;
      const currentPositionRight = currentPositionLeft + timeWindowWidth;

      const timeAtLeftEnd = this.video.duration * (currentPositionLeft / totalScrollableWidth);
      const timeAtRightEnd = this.video.duration * (currentPositionRight / totalScrollableWidth);
      const currentTime = this.video.currentTime;
      if (currentTime <= timeAtLeftEnd) {
        this.editorScrubber.style.left = "0%";
      }
      else if (currentTime >= timeAtRightEnd) {
        this.editorScrubber.style.left = "100%";
      }
      else {
        const normalizedTime = currentTime - timeAtLeftEnd;
        const normalizedEndTime = timeAtRightEnd - timeAtLeftEnd;
        const scrubberPos = normalizedTime / normalizedEndTime * 100;
        this.editorScrubber.style.left = `${scrubberPos}%`;
      }
    }

    handleZoom() {
      const annotationContainers = this.timelineWrapper.querySelectorAll(".track-row-annotations-container");
      if (annotationContainers.length == 0) {
        return;
      }

      const newWidth = `${100 * this.zoomLevel}%`;
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

    updatetimelineScrubber(e) {
        if (!this.timelineScrubber) return;

        const rect = this.timelineTicksContent.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const percent = Math.max(0, Math.min(100, (x / rect.width) * 100));

        this.timelineScrubber.style.left = `${percent}%`;
    }

    // timeline listeners and attachement
    updateTimelineDragPosition(e) {
        if (!this.isDragging) return;

        const rect = this.timelineTicks.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const percent = Math.max(0, Math.min(1, x / rect.width));
        const targetTime = percent * this.duration;

        // Seek the video
        const video = document.querySelector('.annotation-player-container video');
        if (video) {
            video.currentTime = targetTime;
        }

        // Also update via the player API if available
        if (window.videoPlayer && window.videoPlayer.skipTo) {
            window.videoPlayer.skipTo(targetTime);
        }

        e.preventDefault();
    }

    startTimelineDrag(e) {
        this.isDragging = true;
        this.timelineTicks.classList.add('dragging');

        // Get video and check if it was playing
        const video = document.querySelector('.annotation-player-container video');
        if (video) {
            this.wasPlayingBeforeDrag = !video.paused;
            if (this.wasPlayingBeforeDrag) {
                video.pause();
            }
        }

        // Hide hover scrubber during drag
        if (this.timelineScrubber) {
            this.timelineScrubber.style.opacity = '0';
        }

        // Seek to initial position
        this.updateTimelineDragPosition(e);

        e.preventDefault();
    }

    endTimelineDrag() {
        this.isDragging = false;
        this.timelineTicks.classList.remove('dragging');

        // Resume playback if it was playing before
        const video = document.querySelector('.annotation-player-container video');
        if (video && this.wasPlayingBeforeDrag) {
            video.play();
        }

        this.wasPlayingBeforeDrag = false;
    }

    attachTimelineListeners() {
        if (!this.timelineTicks) return;

        this.timelineTicks.addEventListener('mousemove', (e) => {
            if (!this.isDragging) {
                this.updatetimelineScrubber(e);
                // Ensure hover scrubber is visible on mousemove
                if (this.timelineScrubber) {
                    this.timelineScrubber.style.opacity = '1';
                }
            }
        });

        this.timelineTicks.addEventListener('mouseleave', () => {
            if (this.timelineScrubber && !this.isDragging) {
                this.timelineScrubber.style.opacity = '0';
            }
        });

        this.timelineTicks.addEventListener('mouseenter', () => {
            if (this.timelineScrubber && !this.isDragging) {
                this.timelineScrubber.style.opacity = '1';
            }
        });

        this.timelineTicks.addEventListener('mousedown', (e) => {
            this.startTimelineDrag(e);
        });

        document.addEventListener('mousemove', (e) => {
            if (this.isDragging) {
                this.updateTimelineDragPosition(e);
            }
        });

        document.addEventListener('mouseup', () => {
            if (this.isDragging) {
                this.endTimelineDrag();
            }
        });
    }

    updateEditorScrubberPosition(currentTime) {
        if (this.duration <= 0) return;

        const percent = (currentTime / this.duration) * 100;
        if (this.scrubber) {
            this.scrubber.style.setProperty('--scrubber-position', `${percent}%`);
        }
    }

    attachVideoListeners() {
        this.video.addEventListener('timeupdate', () => {
          this.adjustScrubberPosition();
        });
    }

    async handleAnnotationSetChange(event) {
        event.stopPropagation();
        let annotationSetId;
        const selectorOptions = event.target.children;
        for (let option of selectorOptions) {
            if (option.selected) {
                annotationSetId = Number(option.value);
                break;
            }
        }

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
              "X-CSRFToken": this.getCSRFToken(),
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

    setupAnnotationSelectorFunctions() {
        const setSelector = document.getElementById("annotation-set-selector");
        if (!setSelector) {
            console.error("Annotation set selector cannot be found!");
            return;
        }

        setSelector.addEventListener("change", this.handleAnnotationSetChange.bind(this));
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
              "X-CSRFToken": this.getCSRFToken(),
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
          "X-CSRFToken": this.getCSRFToken()
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
          "X-CSRFToken": this.getCSRFToken(),
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
              "X-CSRFToken": this.getCSRFToken(),
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
          "X-CSRFToken": this.getCSRFToken(),
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
