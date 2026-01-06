export class EditorResizer {
    constructor() {
        this.isResizing = false;
        this.currentResizer = null;
        this.startX = 0;
        this.startY = 0;
        this.startWidth = 0;
        this.startHeight = 0;
        this.targetElement = null;

        this.init();
    }

    init() {
        const resizers = document.querySelectorAll('.resizer');

        resizers.forEach(resizer => {
            resizer.addEventListener('mousedown', this.handleMouseDown.bind(this));
        });

        document.addEventListener('mousemove', this.handleMouseMove.bind(this));
        document.addEventListener('mouseup', this.handleMouseUp.bind(this));
    }

    handleMouseDown(e) {
        this.isResizing = true;
        this.currentResizer = e.target;
        this.startX = e.clientX;
        this.startY = e.clientY;

        const direction = this.currentResizer.dataset.direction;

        if (direction === 'horizontal') {
            // Resizing between video and form (vertical resizer, horizontal movement)
            this.targetElement = this.currentResizer.previousElementSibling;
            this.startWidth = this.targetElement.offsetWidth;
            document.body.classList.add('resizing', 'resizing-horizontal');
        } else if (direction === 'vertical') {
            // Resizing between top panel and timeline (horizontal resizer, vertical movement)
            this.targetElement = this.currentResizer.previousElementSibling;
            this.startHeight = this.targetElement.offsetHeight;
            document.body.classList.add('resizing', 'resizing-vertical');
        }

        e.preventDefault();
    }

    handleMouseMove(e) {
        if (!this.isResizing) return;

        const direction = this.currentResizer.dataset.direction;

        if (direction === 'horizontal') {
            // Horizontal resizing (video/form split)
            const deltaX = e.clientX - this.startX;
            const newWidth = this.startWidth + deltaX;

            // Set minimum and maximum widths
            const minWidth = 300;
            const container = this.currentResizer.parentElement;
            const maxWidth = container.offsetWidth - 250; // Leave at least 250px for form

            if (newWidth >= minWidth && newWidth <= maxWidth) {
                this.targetElement.style.flex = `0 0 ${newWidth}px`;
            }
        } else if (direction === 'vertical') {
            // Vertical resizing (top panel/timeline split)
            const deltaY = e.clientY - this.startY;
            const newHeight = this.startHeight + deltaY;

            // Set minimum and maximum heights
            const minHeight = 200;
            const container = this.currentResizer.parentElement;
            const maxHeight = container.offsetHeight - 150; // Leave at least 150px for timeline

            if (newHeight >= minHeight && newHeight <= maxHeight) {
                this.targetElement.style.flex = `0 0 ${newHeight}px`;
            }
        }

        e.preventDefault();
    }

    handleMouseUp(e) {
        if (!this.isResizing) return;

        this.isResizing = false;
        this.currentResizer = null;
        this.targetElement = null;
        document.body.classList.remove('resizing', 'resizing-horizontal', 'resizing-vertical');

        // Trigger window resize event so video player can adjust
        window.dispatchEvent(new Event('resize'));

        e.preventDefault();
    }
}

export class EditorScrubber {
    constructor() {
        this.scrubber = document.querySelector('.editor-scrubber');
        this.layerContent = document.querySelector('.layer-content');
        this.duration = 0;
        this.video = null;

        this.init();
    }

    init() {
        // Wait for video element to be available
        const checkVideo = setInterval(() => {
            this.video = document.querySelector('.annotation-player-container video');
            if (this.video) {
                clearInterval(checkVideo);
                this.attachVideoListeners();
                this.duration = this.video.duration;
            }
        }, 100);
    }

    attachVideoListeners() {
        this.video.addEventListener('timeupdate', () => {
            this.updatePosition(this.video.currentTime);
        });

        this.video.addEventListener('loadedmetadata', () => {
            // Update duration if available from video
            if (this.video.duration) {
                this.duration = this.video.duration;
            }
        });
    }

    updatePosition(currentTime) {
        if (this.duration <= 0) return;

        const percent = (currentTime / this.duration) * 100;
        if (this.scrubber) {
            this.scrubber.style.setProperty('--scrubber-position', `${percent}%`);
        }
    }
}

export class Timeline {
    constructor() {
        this.tickMarksContainer = document.querySelector('.tick-marks-container');
        this.timelineTicks = document.querySelector('.timeline-ticks');
        this.timelineTicksContent = document.querySelector('.timeline-ticks-content');
        this.timelineContentWrapper = document.querySelector('.timeline-content-wrapper');
        this.timelineContainer = document.querySelector('.timeline-container');
        this.layerContent = document.querySelectorAll('.layer-content');
        this.zoomSlider = document.getElementById('zoom-slider');
        this.duration = 0;
        this.zoomLevel = 1;
        this.hoverScrubber = null;
        this.isDragging = false;
        this.wasPlayingBeforeDrag = false;

        this.init();
    }

    init() {
        const video = document.querySelector('.annotation-player-container video');
        this.duration = video.duration;
        this.createHoverScrubber();
        this.renderTickMarks();
        this.attachTimelineListeners();
        this.attachZoomListener();
        this.syncScroll();
        if (this.timelineContainer) {
            this.timelineContainer.style.setProperty('--timeline-zoom', this.zoomLevel);
        }
    }

    attachZoomListener() {
        if (!this.zoomSlider) return;

        this.zoomSlider.addEventListener('input', (e) => {
            const newZoomLevel = parseFloat(e.target.value);
            this.handleZoom(newZoomLevel);
        });
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

        this.renderTickMarks();

        requestAnimationFrame(() => {
            const newContentWidth = this.timelineContainer?.scrollWidth || viewportWidth;
            const newViewportWidth = this.timelineContentWrapper.clientWidth;
            const scrubberPixelPosition = currentPercent * newContentWidth;
            let targetScrollLeft = scrubberPixelPosition - (scrubberViewportRatio * newViewportWidth);
            const maxScrollLeft = Math.max(0, newContentWidth - newViewportWidth);
            targetScrollLeft = Math.max(0, Math.min(targetScrollLeft, maxScrollLeft));
            this.timelineContentWrapper.scrollLeft = targetScrollLeft;
        });
    }

    syncScroll() {
        // No need to sync scroll anymore since we have a single scrollbar
        // Keep this method in case we need it for future enhancements
    }

    createHoverScrubber() {
        this.hoverScrubber = document.querySelector('.timeline-hover-scrubber');
        if (!this.hoverScrubber) {
            this.hoverScrubber = document.createElement('div');
            this.hoverScrubber.className = 'timeline-hover-scrubber';
            if (this.timelineTicksContent) {
                this.timelineTicksContent.appendChild(this.hoverScrubber);
            }
        }
    }

    attachTimelineListeners() {
        if (!this.timelineTicks) return;

        this.timelineTicks.addEventListener('mousemove', (e) => {
            if (!this.isDragging) {
                this.updateHoverScrubber(e);
                // Ensure hover scrubber is visible on mousemove
                if (this.hoverScrubber) {
                    this.hoverScrubber.style.opacity = '1';
                }
            }
        });

        this.timelineTicks.addEventListener('mouseleave', () => {
            if (this.hoverScrubber && !this.isDragging) {
                this.hoverScrubber.style.opacity = '0';
            }
        });

        this.timelineTicks.addEventListener('mouseenter', () => {
            if (this.hoverScrubber && !this.isDragging) {
                this.hoverScrubber.style.opacity = '1';
            }
        });

        this.timelineTicks.addEventListener('mousedown', (e) => {
            this.startDrag(e);
        });

        document.addEventListener('mousemove', (e) => {
            if (this.isDragging) {
                this.updateDragPosition(e);
            }
        });

        document.addEventListener('mouseup', () => {
            if (this.isDragging) {
                this.endDrag();
            }
        });
    }

    startDrag(e) {
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
        if (this.hoverScrubber) {
            this.hoverScrubber.style.opacity = '0';
        }

        // Seek to initial position
        this.updateDragPosition(e);

        e.preventDefault();
    }

    updateDragPosition(e) {
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

    endDrag() {
        this.isDragging = false;
        this.timelineTicks.classList.remove('dragging');

        // Resume playback if it was playing before
        const video = document.querySelector('.annotation-player-container video');
        if (video && this.wasPlayingBeforeDrag) {
            video.play();
        }

        this.wasPlayingBeforeDrag = false;
    }

    updateHoverScrubber(e) {
        if (!this.hoverScrubber) return;

        const rect = this.timelineTicksContent.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const percent = Math.max(0, Math.min(100, (x / rect.width) * 100));

        this.hoverScrubber.style.left = `${percent}%`;
    }

    seekToPosition(e) {
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
    }

    renderTickMarks() {
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

    createTickMark(time, isMajor) {
        const tick = document.createElement('div');
        tick.className = `tick-mark ${isMajor ? 'major' : 'minor'}`;

        const percent = (time / this.duration) * 100;
        tick.style.left = `${percent}%`;

        return tick;
    }

    createTickLabel(time) {
        const label = document.createElement('div');
        label.className = 'tick-label';
        label.textContent = this.formatTime(time);

        const percent = (time / this.duration) * 100;
        label.style.left = `${percent}%`;

        return label;
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
}

export class LayerInteractionHandler {
    constructor() {
        this.layerContainers = document.querySelectorAll('.layer-items');
        this.duration = 0;
        this.dragState = null;
        this.zoomLevel = 1;

        this.init();
    }

    init() {
        const video = document.querySelector('.annotation-player-container video');
        this.duration = video.duration;
        // Event delegation for drag/resize - selection is handled by HTMX attributes
        this.layerContainers.forEach(container => {
            container.addEventListener('mousedown', this.handleMouseDown.bind(this));
        });
        document.addEventListener('mousemove', this.handleMouseMove.bind(this));
        document.addEventListener('mouseup', this.handleMouseUp.bind(this));

        // Listen for zoom changes
        const zoomSlider = document.getElementById('zoom-slider');
        if (zoomSlider) {
            zoomSlider.addEventListener('input', (e) => {
                this.zoomLevel = parseFloat(e.target.value);
            });
        }

        // Listen for "set time" button clicks
        document.addEventListener('click', (e) => {
            if (e.target.dataset.setTime) {
                this.setTimeFromVideo(e.target.dataset.setTime);
            }
        });
        this.placeLayerItems();
        document.body.addEventListener('htmx:afterSettle', this.handleLayerItemPlacementAfterEvent.bind(this));
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

    startDrag(layerItem, e) {
        const layerContainer = layerItem.closest('.layer-items');
        const rect = layerContainer.getBoundingClientRect();
        const containerWidth = layerContainer.scrollWidth || rect.width;

        this.dragState = {
            type: 'drag',
            item: layerItem,
            container: layerContainer,
            startX: e.clientX,
            startLeft: parseFloat(layerItem.style.left),
            containerWidth,
            hasMoved: false,
            originalLeft: parseFloat(layerItem.dataset.originalLeft),
            originalWidth: parseFloat(layerItem.dataset.originalWidth)
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

        this.dragState = {
            type: 'resize',
            item: layerItem,
            container: layerContainer,
            handle,
            isLeft,
            startX: e.clientX,
            startLeft: parseFloat(layerItem.style.left),
            startWidth: parseFloat(layerItem.style.width),
            containerWidth,
            hasMoved: false,
            originalLeft: parseFloat(layerItem.dataset.originalLeft),
            originalWidth: parseFloat(layerItem.dataset.originalWidth)
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

    triggerSave(state) {
        const item = state.item;

        // Find and click the appropriate hidden trigger
        // HTMX attributes on trigger handle the POST automatically
        let trigger;
        if (state.type === 'resize') {
            const handleClass = state.isLeft ? 'resize-handle-left' : 'resize-handle-right';
            trigger = item.querySelector(`.${handleClass}`);
        } else {
            // For drag, just use the first trigger
            trigger = item.querySelector('.resize-handle-left');
        }

        if (trigger) {
            trigger.click();
        } else {
            console.error('Save trigger not found for item:', item.dataset.itemId);
            // Revert on error
            item.style.left = `${state.originalLeft}%`;
            item.style.width = `${state.originalWidth}%`;
            item.dataset.deltaLeft = '0';
            item.dataset.deltaWidth = '0';
        }
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
    }

    handleLayerItemPlacementAfterEvent(e) {
        const classList = e.detail.target.classList;
        const id = e.detail.target.id;
        if (classList.contains('layer-items') || classList.contains("layer-item") || classList.contains("detail-form") || id.includes("item-")) {
            this.placeLayerItems();
        }
    }
}

export class VideoPlayerSync {
    constructor() {
        this.video = null;
        this.jsonContainer = document.getElementById('player-json');

        this.init();
    }

    init() {
        // Wait for video to be available
        const checkVideo = setInterval(() => {
            this.video = document.querySelector('.annotation-player-container video');
            this.player = window.videoPlayer || window.annotationPlayer;
            if (this.video && this.player) {
                clearInterval(checkVideo);
                this.setupJSONWatch();
                this.updatePlayerFromJSON();
            }
        }, 100);
    }

    setupJSONWatch() {
        // Watch for HTMX updates to JSON container
        const observer = new MutationObserver(() => {
            this.updatePlayerFromJSON();
        });

        if (this.jsonContainer) {
            observer.observe(this.jsonContainer, {
                childList: true,
                characterData: true,
                subtree: true
            });
        }

        // Also listen for HTMX afterSwap events
        document.body.addEventListener('htmx:afterSwap', (e) => {
            if (e.detail.target.id === 'player-json') {
                this.updatePlayerFromJSON();
            }
        });

        // Listen for OOB swaps as well
        document.body.addEventListener('htmx:oobAfterSwap', (e) => {
            if (e.detail.target && e.detail.target.id === 'player-json') {
                this.updatePlayerFromJSON();
            }
        });
    }

    updatePlayerFromJSON() {
        if (!this.jsonContainer || !this.player?.loadData) return;
        const data = JSON.parse(this.jsonContainer.textContent);
        this.player.loadData(data);
        this.player.renderSkipsOnScrubber?.();
    }
}


// Helper function for new item creation
window.getNewItemStartEndTimes = function() {
    const video = document.querySelector('.annotation-player-container video');
    const duration = video.duration;

    if (video) {
        const startTime = video.currentTime;
        // Add 20% of duration or 10 seconds, whichever is smaller
        const clipDuration = Math.min(duration * 0.2, 10);
        const endTime = Math.min(startTime + clipDuration, duration);
        return {start_time: startTime, end_time: endTime};
    }
    return {start_time: 0, end_time: Math.min(10, duration)};
};

// Listen for successful item creation to reinitialize interactions
document.body.addEventListener('htmx:afterSwap', function(event) {
    if (event.detail.target.classList?.contains('layer-items')) {
        // Reinitialize layer interaction handler for new items
        const newItems = event.detail.target.querySelectorAll('.layer-item:not([data-initialized])');
        newItems.forEach(item => {
            item.dataset.initialized = 'true';
        });
    }
});

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        new EditorResizer();
        new EditorScrubber();
        new Timeline();
        new LayerInteractionHandler();
        new VideoPlayerSync();
    });
} else {
    new EditorResizer();
    new EditorScrubber();
    new Timeline();
    new LayerInteractionHandler();
    new VideoPlayerSync();
}
