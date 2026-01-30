function convertPercentStringToDecimal(percentString) {
  if (typeof(percentString) === 'string') {
    const newString = percentString.replace('%', '');
    return parseFloat(newString) / 100;
  }

  return;
}

export class Timeline {
    constructor() {
        this.tickMarksContainer = document.querySelector('.tick-marks-container');
        this.timelineTicks = document.querySelector('.timeline-ticks');
        this.timelineTicksContent = document.querySelector('.timeline-ticks-content');
        this.timelineContentWrapper = document.querySelector('.timeline-content-wrapper');
        this.timelineContainer = document.querySelector('.timeline-container');
        this.zoomSlider = document.getElementById('zoom-slider');
        this.scrubber = document.querySelector('.editor-scrubber');
        this.video = document.querySelector('.annotation-player-container video');
        this.duration = this.video.duration;
        this.zoomLevel = 1;
        this.timelineScrubber = null;
        this.isDragging = false;
        this.wasPlayingBeforeDrag = false;

        this.init();
    }

    init() {
        this.renderTickMarksAndLabels();
        this.attachZoomListener();
        this.createtimelineScrubber();
        this.attachTimelineListeners();
        this.attachVideoListeners();
        if (this.timelineContainer) {
            this.timelineContainer.style.setProperty('--timeline-zoom', this.zoomLevel);
        }
    }

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
        label.style.left = `${percent}%`;

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
        this.timelineScrubber = document.querySelector('.timeline-hover-scrubber');
        if (!this.timelineScrubber) {
            this.timelineScrubber = document.createElement('div');
            this.timelineScrubber.className = 'timeline-hover-scrubber';
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

export class LayerInteractionHandler {
    constructor() {
        this.layerContainers = document.querySelectorAll('.layer-items');
        this.video = document.querySelector('.annotation-player-container video');
        this.duration = this.video.duration;
        this.dragState = null;
        this.contentId = null;
        this.listenForNewItemCreation();

        this.init();
    }

    init() {
        const playerContainer = document.getElementById("annotation-player-container");
        this.contentId = playerContainer.dataset["contentid"];
        this.determineLayerItemPositions();
        // Event delegation for drag/resize - selection is handled by HTMX attributes
        this.layerContainers.forEach(container => {
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
        this.placeLayerItems();
        document.body.addEventListener('htmx:afterSettle', this.handleLayerItemPlacementAfterEvent.bind(this));
        this.watchForItemFormChanges();
    }

    placeItem(item) {
      const itemDuration = parseFloat(item.dataset["end"]) - parseFloat(item.dataset["start"]);
      item.style.setProperty("width", `${itemDuration / this.duration * 100}%`);
      item.style.setProperty("left", `${parseFloat(item.dataset["start"]) / this.duration * 100}%`);
    }

    determineLayerItemPositions() {
      this.layerContainers.forEach(layerContainer => {
        const layerItems = Array.from(layerContainer.children);

        for (let item of layerItems) {
          this.placeItem(item);
        }
      });
    }

    handleMouseDown(e) {
        const layerItem = e.target.closest('.layer-item');
        if (!layerItem) return;

        // Always trigger the form load first, regardless of where clicked
        const contentArea = layerItem.querySelector('.layer-item-content');
        if (contentArea) {
            // Use htmx to trigger the GET request to load the form if available,
            // otherwise dispatch a DOM event so other code can listen for it.
            if (window.htmx && typeof window.htmx.trigger === 'function') {
                window.htmx.trigger(contentArea, 'layer-item-click');
            } else {
                const evt = new CustomEvent('layer-item-click', { bubbles: true, cancelable: true });
                contentArea.dispatchEvent(evt);
            }
        }

        const resizeHandle = e.target.closest('.resize-handle');

        if (resizeHandle) {
            this.startResize(layerItem, resizeHandle, e);
            e.preventDefault();
            e.stopPropagation();
        } else if (!e.target.closest('.resize-handle')) {
            this.startDrag(layerItem, e);
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

    startDrag(layerItem, e) {
        const layerContainer = layerItem.closest('.layer-items');
        const rect = layerContainer.getBoundingClientRect();
        const containerWidth = layerContainer.scrollWidth || rect.width;
        const itemLeft = this.calculateItemLeftAsDecimal(layerItem);
        const itemWidth = this.calculateItemWidthAsDecimal(layerItem);

        this.dragState = {
            type: 'drag',
            item: layerItem,
            container: layerContainer,
            startX: e.clientX,
            startLeft: itemLeft * 100,
            containerWidth,
            hasMoved: false,
            originalLeft: itemLeft,
            originalWidth: itemWidth
        };

        // Reset deltas
        layerItem.dataset.deltaLeft = '0';
        layerItem.dataset.deltaWidth = '0';

        layerItem.classList.add('dragging');
        document.body.classList.add('dragging', 'dragging-item');
    }

    startResize(layerItem, handle, e) {
        const isLeft = handle.classList.contains('resize-handle-left');
        const layerContainer = layerItem.closest('.layer-items');
        const rect = layerContainer.getBoundingClientRect();
        const containerWidth = layerContainer.scrollWidth || rect.width;
        const itemLeft = this.calculateItemLeftAsDecimal(layerItem);
        const itemWidth = this.calculateItemWidthAsDecimal(layerItem);

        this.dragState = {
            type: 'resize',
            item: layerItem,
            container: layerContainer,
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
        layerItem.dataset.deltaLeft = '0';
        layerItem.dataset.deltaWidth = '0';

        document.body.classList.add('resizing', 'resizing-item');

        // Seek video to the handle position being dragged
        this.seekToHandlePosition(isLeft, parseFloat(layerItem.style.left), parseFloat(layerItem.style.width));
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
            const width = parseFloat(this.dragState.item.style.width);
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
      const annotationId = itemForm.dataset["itemId"];
      const annotationType = itemForm.dataset["itemType"];
      itemForm.addEventListener("submit", (e) => {
        e.preventDefault();
        this.updateAnnotation(annotationType, annotationId)
      })
    }

    async deleteItem(annotationType, annotationId) {
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
      return await fetch(`/annotations/${annotationType}/${annotationId}/delete/`, {
        method: "delete",
        headers: {"X-CSRFToken": csrfToken}
      });
    }

    setUpItemDeleteButton() {
      const itemForm = document.getElementById("existing-item-form");
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
          const deletedItem = document.getElementById(`${annotationType}-${annotationId}`);
          deletedItem.remove();
          this.placeLayerItems();
        }
      });
    }

    handleItemFormChanges(mutationList) {
      for (let mutation of mutationList) {
        if (mutation.type == "childList") {
          this.listenForItemUpdateFormSubmission();
          this.setUpItemDeleteButton();
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

      if (response.status != 200) {
        console.error("An error occurred while updating an annotation");
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

      const targetForm = document.getElementById("detail-form");
      targetForm.innerHTML = formHtml;
      this.addClickListenerToLayerItem(newTargetItem);
    }

    triggerSave(state) {
        const item = state.item;
        const annotationType = item.dataset["itemType"];
        const annotationId = item.dataset["itemId"];

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
        const newStartTime = leftAsDecimal * this.duration;
        const newEndTime = (leftAsDecimal + convertPercentStringToDecimal(item.style.width)) * this.duration;
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

    async getItemFormDetails(annotationType, annotationId, contentId) {
      const response = await fetch(`/annotations/${annotationType}/${annotationId}/form/?content_id=${contentId}`, {
        method: "GET"
      });
      const detailForm = document.getElementById("detail-form");
      detailForm.innerHTML = await response.text();
    }

    addClickListenerToLayerItem(item) {
      const annotationType = item.dataset["itemType"];
      const annotationId = item.dataset["itemId"];
      item.addEventListener("click", async (e) => {
        e.preventDefault();
        this.getItemFormDetails(annotationType, annotationId, this.contentId);
      });
    }

    setUpItemClickListeners() {
      const layerItems = document.getElementsByClassName("layer-item")
      for (let layerItem of layerItems) {
        this.addClickListenerToLayerItem(layerItem);
      }
    }

    placeLayerItems() {
        // Process each layer container separately
        this.layerContainers.forEach(layerContainer => {
            const layerItems = Array.from(layerContainer.children);
            const layerContainerDim = layerContainer.getBoundingClientRect();
            const itemCount = layerItems.length;

            // Track the bottom of each row (stack)
            let rowBottoms = [];

            // place each layer item
            for (let itemIndex = 0; itemIndex < itemCount; itemIndex++) {
                const currentLayerItem = layerItems[itemIndex];
                const currentItemStart = Number(currentLayerItem.dataset.start);
                const currentItemEnd = Number(currentLayerItem.dataset.end);

                // find the lowest positioned overlapping sibling so we know where to place currentLayerItem
                let allOverlappingSiblings = [];
                let lowestPositionedOverlappingSibling;
                for (let siblingItemIndex = 0; siblingItemIndex < itemIndex; siblingItemIndex++) {
                    const siblingItem = layerItems[siblingItemIndex];
                    if (siblingItem == currentLayerItem) { // this should never happen
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

                // place currentLayerItem if there is an overlapping sibling
                if (lowestPositionedOverlappingSibling) {
                    // check if there is room at the top
                    let isSiblingOccupyingTopSpot = false;
                    for (let sibling of allOverlappingSiblings) {
                        const overLapSibDim = sibling.getBoundingClientRect();
                        if (overLapSibDim.bottom - layerContainerDim.top <= 35) {
                            isSiblingOccupyingTopSpot = true;
                            break;
                        }
                    }
                    if (isSiblingOccupyingTopSpot) {
                        // take the bottom of sibling, subtract the top of the container, add 5 pixels
                        const siblingDim = lowestPositionedOverlappingSibling.getBoundingClientRect();
                        currentLayerItem.style.top = siblingDim.bottom - layerContainerDim.top + 5 + "px";
                    }
                    // else place at the top (default)
                } else {
                    currentLayerItem.style.top = "0px";
                }

                // Track the bottom of this item for stacking calculation
                const itemTop = parseFloat(currentLayerItem.style.top) || 0;
                const itemBottom = itemTop + 30; // item height is 30px
                rowBottoms.push(itemBottom);
            }

            // Calculate the number of stacked rows (find max top value / 35 + 1)
            let maxStack = 1;
            if (layerItems.length > 0) {
                // Find all unique top positions (rounded to nearest 5px)
                const tops = layerItems.map(item => Math.round((parseFloat(item.style.top) || 0) / 5) * 5);
                const uniqueRows = Array.from(new Set(tops));
                maxStack = uniqueRows.length;
            }

            // Set min-height on the parent .layer
            const layer = layerContainer.closest('.layer');
            if (layer) {
                // 1 item = 40px; 2 = 75px; 3 = 110px; 4 = 145px; etc. (diff = 35px)
                const minHeight = (maxStack * 35) + 5;
                layer.style.minHeight = `${minHeight}px`;
            }
        });
      this.setUpItemClickListeners();
    }

    handleLayerItemPlacementAfterEvent(e) {
        const classList = e.detail.target.classList;
        const id = e.detail.target.id;
        if (classList.contains('layer-items') || classList.contains("layer-item") || classList.contains("detail-form") || id.includes("item-")) {
            this.placeLayerItems();
        }
    }

    listenForNewItemCreation() {
      const createItemButtons = document.getElementsByClassName("add-item-btn");
      for (let button of createItemButtons) {
        const annotationType = button.dataset["annotationType"];
        button.addEventListener("click", async (e) => {
          e.preventDefault();
          const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
          const response = await fetch(`/annotations/${annotationType}/create/content/${this.contentId}/`,
            {
              method: "POST",
              headers: {"X-CSRFToken": csrfToken}
            });
          if (response.ok) {
            const newItemHtml = await response.text();
            const layerContainer = document.getElementById(`${annotationType}-item-container`);
            const newElement = document.createElement("template");
            newElement.innerHTML = newItemHtml;
            const newNode = newElement.content.firstChild;
            layerContainer.append(newNode);
            this.addClickListenerToLayerItem(newNode);

            let startTime = 0;
            let endTime = 10;
            if (this.video) {
                startTime = this.video.currentTime;
                // Make sure new item can fit on the page
                const itemDuration = Math.min(this.duration * 0.2, 10);
                endTime = Math.min(startTime + itemDuration, this.duration);
            }
            newNode.dataset["start"] = startTime;
            newNode.dataset["end"] = endTime;
          }
          else {
            console.error(response);
          }
        })
      }
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
  if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
          new Timeline();
          new LayerInteractionHandler();
      });
  } else {
      new Timeline();
      new LayerInteractionHandler();
  }
  setupAnnotationSelectorFunctions();
}

const checkVideo = setInterval(() => {
    const video = document.querySelector('.annotation-player-container video');
    if (video && !isNaN(video.duration)) {
      clearInterval(checkVideo);
      editorInit();
    }
}, 100);
