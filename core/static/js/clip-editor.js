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
            this.layerContent = document.querySelector('.layer-content');
            this.duration = parseFloat(document.querySelector('.clip-editor-container').dataset.duration) || 120;
            this.zoomLevel = 1; // 1x to 10x scale

            this.init();
        }

        init() {
            this.renderTickMarks();

            // Re-render tick marks when zoom changes (future enhancement)
            window.addEventListener('timeline:zoom', (e) => {
                this.zoomLevel = e.detail.zoomLevel;
                this.renderTickMarks();
            });
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

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            new ClipEditorResizer();
            new ClipEditorScrubber();
            new Timeline();
        });
    } else {
        new ClipEditorResizer();
        new ClipEditorScrubber();
        new Timeline();
    }
})();
