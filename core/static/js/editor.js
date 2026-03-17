function convertPercentStringToDecimal(percentString) {
  if (typeof(percentString) === 'string') {
    const newString = percentString.replace('%', '');
    return parseFloat(newString) / 100;
  }

  return;
}

export class Editor {
    constructor() {
        this.tracks = document.querySelectorAll('.timeline-track-row-right');
        this.video = document.querySelector('.annotation-player-container video');
        this.duration = this.video.duration;
        this.annotationBox = window.videoPlayer.annotationBox;
        this.dragState = null;
        this.contentId = null;
        this.listenForNewItemCreation();
        this.typeOfAnnotationInFocus = null;
        this.annotationIdInFocus = null;
        this.activeCensorPosition = null;

        this.tickMarksContainer = document.querySelector('.tick-marks-container');
        this.timelineTicks = document.querySelector('.timeline-ticks');
        this.timelineTicksContent = document.querySelector('.timeline-ticks-content');
        this.timelineContentWrapper = document.querySelector('.timeline-content-wrapper');
        this.timelineContainer = document.querySelector('.timeline-container');
        this.zoomSlider = document.getElementById('zoom-slider');
        this.scrubber = document.querySelector('#editor-scrubber');
        this.zoomLevel = 1;
        this.timelineScrubber = null;
        this.isDragging = false;
        this.wasPlayingBeforeDrag = false;

        this.annotationUpdatedEvent = new CustomEvent("annotationUpdated");

        this.init();
    }

    init() {
        const playerContainer = document.getElementById("annotation-player-container");
        this.contentId = playerContainer.dataset["contentid"];
        this.determineTrackItemPositions();
        // Event delegation for drag/resize - selection is handled by HTMX attributes
        this.tracks.forEach(container => {
            container.addEventListener('mousedown', this.handleMouseDown.bind(this));
        });
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
        if (this.timelineContainer) {
            this.timelineContainer.style.setProperty('--timeline-zoom', this.zoomLevel);
        }
    }

    placeItem(item) {
      const itemDuration = parseFloat(item.dataset["end"]) - parseFloat(item.dataset["start"]);
      if (item.dataset.annotationType != "pause") {
        item.style.setProperty("width", `${itemDuration / this.duration * 100}%`);
      }
      item.style.setProperty("left", `${parseFloat(item.dataset["start"]) / this.duration * 100}%`);
    }

    determineTrackItemPositions() {
      this.tracks.forEach(track => {
        const trackItems = Array.from(track.children);

        for (let item of trackItems) {
          this.placeItem(item);
        }
      });
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
        const itemContainer = trackItem.closest('.timeline-row-right');
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
        const itemContainer = trackItem.closest('.timeline-row-right');
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
        this.updateAnnotation(annotationType, annotationId)
      })
    }

    async deleteItem(annotationType, annotationId) {
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
      return await fetch(`/annotations/${annotationType}/${annotationId}/delete`, {
        method: "delete",
        headers: {"X-CSRFToken": csrfToken}
      });
    }

    async setUpItemDeleteButton() {
      const itemForm = document.getElementById("existing-item-form");
      if (!itemForm) {
        return;
      }
      const annotationType = itemForm.dataset["itemtype"];
      const annotationId = itemForm.dataset["annotationid"];
      const deleteItemButton = itemForm.querySelector("#annotation-form-delete-button");
      deleteItemButton.addEventListener("click", async (e) => {
        e.preventDefault();
        const response = await this.deleteItem(annotationType, annotationId);
        if (!response.ok) {
          console.error("The item could not be deleted");
        }
        else {
          if(this.typeOfAnnotationInFocus == "censor") {
            this.handleFocusChangeAwayFromCensorType();
          }
          window.dispatchEvent(this.annotationUpdatedEvent);
          const panelItemToRemove = document.getElementById(`${annotationType}-panel-item-${annotationId}`);
          if (panelItemToRemove) {
            panelItemToRemove.remove();
          }

          const deletedItem = document.getElementById(`${annotationType}-${annotationId}`);
          deletedItem.remove();
          const detailForm = document.getElementById("detail-form");
          detailForm.innerHTML = "";
          this.placeTrackItems();
        }
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
        positions.push({
          "id": positionEl.dataset["positionId"],
          "time": parseFloat(positionEl.querySelector(".position-time-input").value).toFixed(2),
        });
      }
      return positions;
    }

    placeNewCensorPositionHtml(censor_parent_id, html) {
      const annotationUpdateForm = document.getElementById("existing-item-form");
      const currentFormId = annotationUpdateForm.dataset["annotationid"];
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
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
      const response = await fetch("/annotations/censor-position/create", {
        method: "POST",
        headers: {"X-CSRFToken": csrfToken, "Content-Type": "application/json"},
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
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
      const response = await fetch("/annotations/censor-position/update", {
        method: "POST",
        headers: {"X-CSRFToken": csrfToken, "Content-Type": "application/json"},
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
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
      const response = await fetch(`/annotations/censor-position/delete/${positionId}`, {
        method: "DELETE",
        headers: {"X-CSRFToken": csrfToken}
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
      const annotationId = itemForm.dataset["annotationid"];

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

    handleCensorPointerDown(e) {
      e.preventDefault();
      e.stopPropagation();
      const censorEl = e.currentTarget;
      const censorLeftStart = censorEl.style.left;
      const censorTopStart = censorEl.style.top;
      const censorPointerId = e.pointerId;
      censorEl.setPointerCapture(censorPointerId);
      const annotationBox = document.getElementById("annotation-box");
      const boxRect = annotationBox.getBoundingClientRect();
      const widthPercent = parseFloat(censorEl.style.width);
      const heightPercent = parseFloat(censorEl.style.height);

      function handleCensorMove(event) {
        const xPercent = (event.clientX - boxRect.left) / boxRect.width * 100;
        const yPercent = (event.clientY - boxRect.top) / boxRect.height * 100;
        censorEl.style.left = `${Math.max(0, Math.min(100 - widthPercent, xPercent - widthPercent / 2))}%`;
        censorEl.style.top = `${Math.max(0, Math.min(100 - heightPercent, yPercent - heightPercent / 2))}%`;
      }

      async function onPointerUp(upEvent) {
        handleCensorMove(upEvent);
        const positionEl = upEvent.target;
        const censorPositionId = positionEl.dataset["censorPositionId"];
        const parentCensorId = positionEl.dataset["censorPositionParentId"];
        const newX = ((upEvent.clientX - boxRect.left) / boxRect.width * 100) - widthPercent / 2;
        const newY = ((upEvent.clientY - boxRect.top) / boxRect.height * 100) - heightPercent / 2;
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

      function handleCleanup() {
        censorEl.releasePointerCapture(censorPointerId);
        censorEl.removeEventListener('pointermove', handleCensorMove);
        censorEl.removeEventListener('pointerup', pointerUpCallback);
        censorEl.removeEventListener('pointercancel', handleMoveCancel);
        document.removeEventListener("keyup", handleEscKeyPress);
      }

      censorEl.addEventListener('pointermove', handleCensorMove);
      censorEl.addEventListener('pointerup', pointerUpCallback);
      censorEl.addEventListener('pointercancel', handleMoveCancel);
      document.addEventListener("keyup", handleEscKeyPress);
    }


    handleFocusChangeToCensorType() {
      this.annotationBox.className = "annotation-box annotation-box-censor-editor";
      this.annotationBoxCensorListener = this.handleCensorAnnotationBoxClick.bind(this);
      this.annotationBox.addEventListener("click", this.annotationBoxCensorListener);
      const censorPositions = document.getElementsByClassName("censor-position");
      for (let position of censorPositions) {
        if (position.dataset["censorPositionParentId"] == this.annotationIdInFocus) {
          this.activeCensorPosition = position;
          this.activeCensorPosition.classList.toggle("active-censor-position");
          break;
        }
      }
      if (this.activeCensorPosition) {
        function buildSizeEditPoints(censorPositionElement, editor) {
          const MIN_SIZE = 3; // minimum percent size
          const annotationBox = document.getElementById("annotation-box");
          const cornerData = [
            { cls: "top-left-point",     movesLeft: true,  movesTop: true  },
            { cls: "top-right-point",    movesLeft: false, movesTop: true  },
            { cls: "bottom-left-point",  movesLeft: true,  movesTop: false },
            { cls: "bottom-right-point", movesLeft: false, movesTop: false },
          ];

          for (const { cls, movesLeft, movesTop } of cornerData) {
            const point = document.createElement("div");
            point.className = `censor-position-adjustment-point ${cls}`;

            point.addEventListener("pointerdown", function(ptrDownEvent) {
              ptrDownEvent.stopPropagation();
              ptrDownEvent.preventDefault();
              point.setPointerCapture(ptrDownEvent.pointerId);

              const boxRect = annotationBox.getBoundingClientRect();
              const startLeft = censorPositionElement.style.left;
              const startTop = censorPositionElement.style.top;
              const startWidth = censorPositionElement.style.width;
              const startHeight = censorPositionElement.style.height;
              let resizeCancelled = false;

              function onMove(ptrMoveEvent) {
                const newX = (ptrMoveEvent.clientX - boxRect.left) / boxRect.width * 100;
                const newY = (ptrMoveEvent.clientY - boxRect.top) / boxRect.height * 100;
                const curLeft = parseFloat(censorPositionElement.style.left);
                const curTop = parseFloat(censorPositionElement.style.top);
                const curWidth = parseFloat(censorPositionElement.style.width);
                const curHeight = parseFloat(censorPositionElement.style.height);
                const fixedRight = curLeft + curWidth;
                const fixedBottom = curTop + curHeight;

                if (movesLeft) {
                  const newLeft = Math.max(0, Math.min(newX, fixedRight - MIN_SIZE));
                  censorPositionElement.style.left = `${newLeft}%`;
                  censorPositionElement.style.width = `${fixedRight - newLeft}%`;
                } else {
                  censorPositionElement.style.width = `${Math.max(MIN_SIZE, Math.min(newX - curLeft, 100 - curLeft))}%`;
                }

                if (movesTop) {
                  const newTop = Math.max(0, Math.min(newY, fixedBottom - MIN_SIZE));
                  censorPositionElement.style.top = `${newTop}%`;
                  censorPositionElement.style.height = `${fixedBottom - newTop}%`;
                } else {
                  censorPositionElement.style.height = `${Math.max(MIN_SIZE, Math.min(newY - curTop, 100 - curTop))}%`;
                }
              }

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

                const newLeft = parseFloat(censorPositionElement.style.left);
                const newTop = parseFloat(censorPositionElement.style.top);
                const newWidth = parseFloat(censorPositionElement.style.width);
                const newHeight = parseFloat(censorPositionElement.style.height);
                const positionId = censorPositionElement.dataset["censorPositionId"];
                const parentId = censorPositionElement.dataset["censorPositionParentId"];
                await editor.updateCensorPosition(positionId, editor.video.currentTime, newLeft, newTop, newWidth, newHeight, parentId);
              }

              function onCancel() {
                censorPositionElement.style.left = startLeft;
                censorPositionElement.style.top = startTop;
                censorPositionElement.style.width = startWidth;
                censorPositionElement.style.height = startHeight;
                handleCleanup();
              }

              function handleEscKeyPress(keyupEvent) {
                if (keyupEvent.defaultPrevented) {
                  return;
                }
                if (keyupEvent.key === "Escape") {
                  resizeCancelled = true;
                  censorPositionElement.style.left = startLeft;
                  censorPositionElement.style.top = startTop;
                  censorPositionElement.style.width = startWidth;
                  censorPositionElement.style.height = startHeight;
                  point.removeEventListener("pointermove", onMove);
                  document.removeEventListener("keyup", handleEscKeyPress);
                }
              }

              point.addEventListener("pointermove", onMove);
              point.addEventListener("pointerup", onPointerUp);
              point.addEventListener("pointercancel", onCancel);
              document.addEventListener("keyup", handleEscKeyPress);
            });

            censorPositionElement.appendChild(point);
          }
        }

        buildSizeEditPoints(this.activeCensorPosition, this);
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

      this.typeOfAnnotationInFocus = itemForm.dataset["itemtype"];
      this.annotationIdInFocus = itemForm.dataset["annotationid"];

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
          this.setUpItemDeleteButton();
          this.changeAnnotationInFocus();
        }
      }
    }

    watchForItemFormChanges() {
      const itemFormObserver = new MutationObserver(this.handleItemFormChanges.bind(this))
      const itemForm = document.getElementById("detail-form");
      itemFormObserver.observe(itemForm, { childList: true });
    }

    async updateAnnotation(annotationType, annotationId, name=undefined, description=undefined, startTime=undefined, endTime=undefined, isFromItem=false) {
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
      const isFromItemValue = Number(isFromItem)

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

      const response = await fetch(`/annotations/${annotationType}/${annotationId}/${isFromItemValue}/update/`, {
        method: "POST",
        headers: {
          "X-CSRFToken": csrfToken,
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

      const targetItem = document.getElementById(`${annotationType}-${annotationId}`);
      targetItem.outerHTML = itemHtml;
      // if you pass the previous targetItem into this.placeItem, it will make changes to an
      // element that no longer exists. You must get the new element before making style changes.
      const newTargetItem = document.getElementById(`${annotationType}-${annotationId}`);
      this.placeItem(newTargetItem);
      this.placeTrackItems();

      const targetForm = document.getElementById("detail-form");
      targetForm.innerHTML = formHtml;
      this.fetchEditFormOnClick(newTargetItem);
      window.dispatchEvent(this.annotationUpdatedEvent);
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
        this.updateAnnotation(annotationType, annotationId, undefined, undefined, newStartTime, newEndTime, true);
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

    setupCensorPositionSeekListeners() {
      const handler = (clickEvent) => {
        this.video.currentTime = clickEvent.target.parentElement.querySelector(".position-time-input").value;
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

    async getItemFormDetails(annotationType, annotationId, contentId) {
      const response = await fetch(`/annotations/${annotationType}/${annotationId}/form/?content_id=${contentId}`, {
        method: "GET"
      });
      const detailForm = document.getElementById("detail-form");
      detailForm.innerHTML = await response.text();
      this.setUpCensorPositionDeleteListeners(annotationId);
      this.setupCensorPositionSeekListeners();
    }

    markPanelItemAsActive(annotationType, annotationId) {
      if (!annotationType || !annotationId) {
        console.error("Invalid annotation type of annotation id");
        return;
      }
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
      const thisItemList = thisPanelItem.closest(".annotation-type-list");
      thisItemList.classList.add(itemListExpansionClass);
      const thisGroupWrapper = thisPanelItem.closest(".annotation-type-wrapper");
      const thisGroupArrow = thisGroupWrapper.querySelector(".annotation-type-header-arrow");
      thisGroupArrow.classList.add(arrowRotationClass);
    }

    fetchEditFormOnClick(element) {
      const annotationType = element.dataset["annotationType"];
      const annotationId = element.dataset["annotationId"];
      element.addEventListener("click", async (e) => {
        e.preventDefault();
        this.getItemFormDetails(annotationType, annotationId, this.contentId);
        this.markPanelItemAsActive(annotationType, annotationId);
      });
    }

    setUpItemClickListeners() {
      const trackItems = document.getElementsByClassName("track-item");
      for (let trackItem of trackItems) {
        this.fetchEditFormOnClick(trackItem);
      }

      const annotationPanelItems = document.getElementsByClassName("annotation-list-item-wrapper");
      for (let panelItem of annotationPanelItems) {
        this.fetchEditFormOnClick(panelItem);
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

    placeTrackItems() {
        // Process each track container separately
        this.tracks.forEach(track => {
            const trackItems = Array.from(track.children);
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
      this.setUpItemClickListeners();
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

            let startTime = 0;
            let endTime = 0;
            if (this.video) {
                startTime = this.video.currentTime;
                // Make sure new item can fit on the page
                const itemDuration = Math.min(this.duration * 0.2, 10);
                endTime = Math.min(startTime + itemDuration, this.duration);
            }

            const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
            const response = await fetch(`/annotations/${annotationType}/create/content/${this.contentId}`,
              {
                method: "POST",
                headers: {"X-CSRFToken": csrfToken},
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
              const trackContainer = document.getElementById(`${annotationType}-item-container`);
              const newElement = document.createElement("template");
              newElement.innerHTML = newTrackItemHtml;
              const newNode = newElement.content.firstChild;
              newNode.dataset["start"] = startTime;
              newNode.dataset["end"] = endTime;
              trackContainer.append(newNode);
              this.fetchEditFormOnClick(newNode);
              this.placeItem(newNode);
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
            const isMajor = Math.abs(time % interval) < 0.01;
            const tick = this.createTickMark(time, isMajor);
            this.tickMarksContainer.appendChild(tick);

            // Add label for major ticks
            if (isMajor) {
                const label = this.createTickLabel(time);
                this.tickMarksContainer.appendChild(label);
            }
        }
    }

    handleZoom(newZoomLevel) {
        const video = document.querySelector('.annotation-player-container video');
        const currentTime = video?.currentTime || 0;
        const currentPercent = this.duration > 0 ? currentTime / this.duration : 0;

        const viewportWidth = this.timelineContentWrapper.clientWidth;
        const oldContentWidth = this.timelineContainer?.scrollWidth || viewportWidth;
        const scrollLeft = this.timelineContentWrapper.scrollLeft;
        const scrubberPixelPositionOld = currentPercent * oldContentWidth;
        let scrubberViewportRatio = viewportWidth
            ? (scrubberPixelPositionOld - scrollLeft) / viewportWidth
            : 0;
        scrubberViewportRatio = Math.max(0, Math.min(1, scrubberViewportRatio));

        this.zoomLevel = newZoomLevel;

        if (this.timelineContainer) {
            this.timelineContainer.style.setProperty('--timeline-zoom', newZoomLevel);
        }

        this.renderTickMarksAndLabels();

        const newContentWidth = this.timelineContainer?.scrollWidth || viewportWidth;
        const newViewportWidth = this.timelineContentWrapper.clientWidth;
        const scrubberPixelPosition = currentPercent * newContentWidth;
        let targetScrollLeft = scrubberPixelPosition - (scrubberViewportRatio * newViewportWidth);
        const maxScrollLeft = Math.max(0, newContentWidth - newViewportWidth);
        targetScrollLeft = Math.max(0, Math.min(targetScrollLeft, maxScrollLeft));
        this.timelineContentWrapper.scrollLeft = targetScrollLeft;
    }

    attachZoomListener() {
        if (!this.zoomSlider) return;

        this.zoomSlider.addEventListener('input', (e) => {
            const newZoomLevel = parseFloat(e.target.value);
            this.handleZoom(newZoomLevel);
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
            this.updateEditorScrubberPosition(this.video.currentTime);
        });
    }
}

async function handleAnnotationSetChange(event) {
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

    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    if (!this.contentId) {
        console.error("could not retrieve content id while switching annotation sets!");
        return;
    }
    const htmlContentResponse = await fetch("/select-annotation-set", {
        method: "POST",
        body: JSON.stringify({"annotation_set_id": annotationSetId, "content_id": this.contentId}),
        headers: {"X-CSRFToken": csrfToken},
        mode: "same-origin"
    });

    const newHTMLContent = await htmlContentResponse.json();

    const videoSection = document.getElementById("video-section");
    videoSection.innerHTML = newHTMLContent["video_section"];

    const timelineLayers = document.getElementById("annotation-timeline");
    timelineLayers.innerHTML = newHTMLContent["timeline_layers"];
}

function setupAnnotationSelectorFunctions() {
    const setSelector = document.getElementById("annotation-set-selector");
    if (!setSelector) {
        console.error("Annotation set selector cannot be found!");
        return;
    }

    setSelector.addEventListener("change", handleAnnotationSetChange);
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
  setupAnnotationSelectorFunctions();
}

const checkVideo = setInterval(() => {
    const video = document.querySelector('.annotation-player-container video');
    if (video && !isNaN(video.duration) && window?.videoPlayer) {
      clearInterval(checkVideo);
      editorInit();
    }
}, 100);
