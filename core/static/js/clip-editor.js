(function() {
    'use strict';

    class ClipEditorResizer {
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

    class ClipEditorScrubber {
        constructor() {
            this.scrubber = document.querySelector('.editor-scrubber');
            this.layerContent = document.querySelector('.layer-content');
            this.duration = parseFloat(document.querySelector('.clip-editor-container').dataset.duration) || 120;
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

    class Timeline {
        constructor() {
            this.tickMarksContainer = document.querySelector('.tick-marks-container');
            this.timelineTicks = document.querySelector('.timeline-ticks');
            this.layerContent = document.querySelector('.layer-content');
            this.duration = parseFloat(document.querySelector('.clip-editor-container').dataset.duration) || 120;
            this.zoomLevel = 1; // 1x to 10x scale
            this.hoverScrubber = null;
            this.isDragging = false;
            this.wasPlayingBeforeDrag = false;

            this.init();
        }

        init() {
            this.createHoverScrubber();
            this.renderTickMarks();
            this.attachTimelineListeners();

            // Re-render tick marks when zoom changes (future enhancement)
            window.addEventListener('timeline:zoom', (e) => {
                this.zoomLevel = e.detail.zoomLevel;
                this.renderTickMarks();
            });
        }

        createHoverScrubber() {
            if (!this.timelineTicks) return;

            this.hoverScrubber = document.createElement('div');
            this.hoverScrubber.className = 'timeline-hover-scrubber';
            this.timelineTicks.appendChild(this.hoverScrubber);
        }

        attachTimelineListeners() {
            if (!this.timelineTicks) return;

            this.timelineTicks.addEventListener('mousemove', (e) => {
                if (!this.isDragging) {
                    this.updateHoverScrubber(e);
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

            const rect = this.timelineTicks.getBoundingClientRect();
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

    class LayerInteractionHandler {
        constructor() {
            this.layerContainer = document.querySelector('.layer-items');
            this.duration = parseFloat(document.querySelector('.clip-editor-container').dataset.duration) || 120;
            this.dragState = null;

            this.init();
        }

        init() {
            // Event delegation for drag/resize - selection is handled by HTMX attributes
            this.layerContainer.addEventListener('mousedown', this.handleMouseDown.bind(this));
            document.addEventListener('mousemove', this.handleMouseMove.bind(this));
            document.addEventListener('mouseup', this.handleMouseUp.bind(this));

            // Listen for "set time" button clicks
            document.addEventListener('click', (e) => {
                if (e.target.dataset.setTime) {
                    this.setTimeFromVideo(e.target.dataset.setTime);
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

            const resizeHandle = e.target.closest('.resize-handle:not(.resize-trigger)');

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
            const rect = this.layerContainer.getBoundingClientRect();

            this.dragState = {
                type: 'drag',
                item: layerItem,
                startX: e.clientX,
                startLeft: parseFloat(layerItem.style.left),
                containerWidth: rect.width,
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

            this.dragState = {
                type: 'resize',
                item: layerItem,
                handle: handle,
                isLeft: isLeft,
                startX: e.clientX,
                startLeft: parseFloat(layerItem.style.left),
                startWidth: parseFloat(layerItem.style.width),
                containerWidth: this.layerContainer.offsetWidth,
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
            const deltaPercent = (deltaX / this.dragState.containerWidth) * 100;
            let newLeft = this.dragState.startLeft + deltaPercent;

            // Constrain to 0-100%
            const width = parseFloat(this.dragState.item.style.width);
            newLeft = Math.max(0, Math.min(newLeft, 100 - width));

            this.dragState.item.style.left = `${newLeft}%`;

            // Store delta from original position
            const deltaFromOriginal = newLeft - this.dragState.originalLeft;
            this.dragState.item.dataset.deltaLeft = deltaFromOriginal.toFixed(2);

            // Seek video to the left edge of the item
            const video = document.querySelector('.annotation-player-container video');
            if (video) {
                const targetTime = (newLeft / 100) * this.duration;
                video.currentTime = targetTime;

                // Also update via the player API if available
                if (window.videoPlayer && window.videoPlayer.skipTo) {
                    window.videoPlayer.skipTo(targetTime);
                }
            }
        }

        updateResizePosition(e) {
            const deltaX = e.clientX - this.dragState.startX;
            const deltaPercent = (deltaX / this.dragState.containerWidth) * 100;

            if (this.dragState.isLeft) {
                // Resize from left
                let newLeft = this.dragState.startLeft + deltaPercent;
                let newWidth = this.dragState.startWidth - deltaPercent;

                // Constrain
                newLeft = Math.max(0, newLeft);
                newWidth = Math.max(1, newWidth); // Minimum 1% width

                // Don't extend past right edge
                if (newLeft + newWidth > 100) {
                    newWidth = 100 - newLeft;
                }

                this.dragState.item.style.left = `${newLeft}%`;
                this.dragState.item.style.width = `${newWidth}%`;

                // Store deltas from original
                const deltaLeft = newLeft - this.dragState.originalLeft;
                const deltaWidth = newWidth - this.dragState.originalWidth;
                this.dragState.item.dataset.deltaLeft = deltaLeft.toFixed(2);
                this.dragState.item.dataset.deltaWidth = deltaWidth.toFixed(2);

                // Seek video to left handle position
                this.seekToHandlePosition(true, newLeft, newWidth);
            } else {
                // Resize from right
                let newWidth = this.dragState.startWidth + deltaPercent;

                // Constrain
                newWidth = Math.max(1, newWidth);
                const maxWidth = 100 - this.dragState.startLeft;
                newWidth = Math.min(newWidth, maxWidth);

                this.dragState.item.style.width = `${newWidth}%`;

                // Store delta from original width (left unchanged)
                const deltaWidth = newWidth - this.dragState.originalWidth;
                this.dragState.item.dataset.deltaWidth = deltaWidth.toFixed(2);

                // Seek video to right handle position
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
            const clipId = item.dataset.itemId;

            // Find and click the appropriate hidden trigger
            // HTMX attributes on trigger handle the POST automatically
            let trigger;
            if (state.type === 'resize') {
                const handleClass = state.isLeft ? 'resize-handle-left' : 'resize-handle-right';
                trigger = item.querySelector(`.${handleClass}.resize-trigger`);
            } else {
                // For drag, just use the first trigger
                trigger = item.querySelector('.resize-trigger');
            }

            if (trigger) {
                trigger.click();
            } else {
                console.error('Save trigger not found for item:', clipId);
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
    }

    class VideoPlayerSync {
        constructor() {
            this.video = null;
            this.jsonContainer = document.getElementById('clips-json');

            this.init();
        }

        init() {
            // Wait for video to be available
            const checkVideo = setInterval(() => {
                this.video = document.querySelector('.annotation-player-container video');
                if (this.video && window.annotationPlayer) {
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
                if (e.detail.target.id === 'clips-json') {
                    this.updatePlayerFromJSON();
                }
            });
        }

        updatePlayerFromJSON() {
            if (!this.jsonContainer || !window.annotationPlayer) return;

            try {
                const clipsData = JSON.parse(this.jsonContainer.textContent);

                // Update the player's clips
                if (window.annotationPlayer.updateClips) {
                    window.annotationPlayer.updateClips(clipsData);
                } else if (window.annotationPlayer.setClips) {
                    window.annotationPlayer.setClips(clipsData);
                }
            } catch (e) {
                console.error('Failed to parse clips JSON:', e);
            }
        }
    }

    // Helper functions for new clip creation
    window.getNewClipStartTime = function() {
        const video = document.querySelector('.annotation-player-container video');
        if (video) {
            return video.currentTime;
        }
        return 0;
    };

    window.getNewClipEndTime = function() {
        const video = document.querySelector('.annotation-player-container video');
        const container = document.querySelector('.clip-editor-container');
        const duration = parseFloat(container?.dataset.duration) || 120;

        if (video) {
            const startTime = video.currentTime;
            // Add 20% of duration or 10 seconds, whichever is smaller
            const clipDuration = Math.min(duration * 0.2, 10);
            const endTime = Math.min(startTime + clipDuration, duration);
            return endTime;
        }
        return Math.min(10, duration);
    };

    // Listen for successful clip creation to reinitialize interactions
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
            new ClipEditorResizer();
            new ClipEditorScrubber();
            new Timeline();
            new LayerInteractionHandler();
            new VideoPlayerSync();
        });
    } else {
        new ClipEditorResizer();
        new ClipEditorScrubber();
        new Timeline();
        new LayerInteractionHandler();
        new VideoPlayerSync();
    }
})();
