import { LayerInteractionHandler, Timeline, TimelineScrubber, VideoPlayerSync } from './timeline-common.js';

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
            new TimelineScrubber();
            new Timeline();
            new LayerInteractionHandler();
            new VideoPlayerSync();
        });
    } else {
        new ClipEditorResizer();
        new TimelineScrubber();
        new Timeline();
        new LayerInteractionHandler();
        new VideoPlayerSync();
    }
})();
