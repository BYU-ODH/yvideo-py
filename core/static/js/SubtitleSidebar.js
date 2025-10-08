/**
 * Subtitle sidebar cue class
 * - Represents a single cue in the sidebar
 */
class SubtitleSidebarCue {
  constructor(cue, index) {
    this.cue = cue;
    this.index = index;
    this.element = null;
  }

  render() {
    const cueElement = document.createElement('div');
    cueElement.className = 'subtitle-cue';
    cueElement.dataset.index = this.index;
    cueElement.dataset.startTime = this.cue.startTime;

    const timestamp = this._formatTime(this.cue.startTime);

    cueElement.innerHTML = `
      <button class="subtitle-cue-seek" aria-label="Seek to ${timestamp}">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <path d="M4 3l8 5-8 5V3z"/>
        </svg>
      </button>
      <div class="subtitle-cue-content">
        <div class="subtitle-cue-time">${timestamp}</div>
        <div class="subtitle-cue-text">${this._stripVTTFormatting(this.cue.text)}</div>
      </div>
    `;

    this.element = cueElement;
    return cueElement;
  }

  _stripVTTFormatting(text) {
    return text.replace(/<[^>]*>/g, '').trim();
  }

  _formatTime(seconds) {
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    if (hrs > 0) {
      return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }

  isActive(currentTime) {
    return currentTime >= this.cue.startTime && currentTime < this.cue.endTime;
  }
}

/**
 * Subtitle sidebar class
 * - Manages loading and displaying subtitles in a scrollable sidebar
 * - Highlights current subtitle based on video time
 * - Clicking on a subtitle's seek icon jumps video to 0.09 seconds before that subtitle
 * - Uses videoElem.textTracks as the single source of truth for which track is active
 */
export class SubtitleSidebar {
  constructor(videoElem) {
    this.videoElem = videoElem;
    this.visible = false;
    this.cueObjects = [];
    this.isResizing = false;
    this.startX = 0;
    this.startWidth = 0;

    // Load saved width from localStorage or use default
    this.width = parseInt(localStorage.getItem('subtitleSidebarWidth')) || 320;

    this._createSidebar();
    this._initEventListeners();
    this._initResizeListeners();
  }

  _createSidebar() {
    // Create sidebar container
    this.sidebar = document.createElement('div');
    this.sidebar.className = 'subtitle-sidebar';
    this.sidebar.style.width = `${this.width}px`;

    // Create resize handle
    const resizeHandle = document.createElement('div');
    resizeHandle.className = 'subtitle-sidebar-resize-handle';
    this.resizeHandle = resizeHandle;

    // Create header
    const header = document.createElement('div');
    header.className = 'subtitle-sidebar-header';
    header.innerHTML = `
      <h3>Transcript</h3>
      <button class="subtitle-sidebar-close" aria-label="Close Transcript">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
          <path d="M15 5L5 15M5 5l10 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </button>
    `;

    // Create content area
    this.content = document.createElement('div');
    this.content.className = 'subtitle-sidebar-content';

    this.sidebar.appendChild(resizeHandle);
    this.sidebar.appendChild(header);
    this.sidebar.appendChild(this.content);

    // Insert sidebar into player container
    const container = this.videoElem.closest('.annotation-player-container');
    if (container) {
      container.appendChild(this.sidebar);
      console.log('Subtitle sidebar added to container');
    } else {
      document.body.appendChild(this.sidebar);
      console.warn('Player container not found, appending sidebar to body');
    }

    // Close button handler
    const closeBtn = header.querySelector('.subtitle-sidebar-close');
    closeBtn.addEventListener('click', () => this.hide());
  }

  _initResizeListeners() {
    this.resizeHandle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      this.isResizing = true;
      this.startX = e.clientX;
      this.startWidth = this.sidebar.offsetWidth;
      this.resizeHandle.classList.add('resizing');

      // Disable transitions for immediate feedback
      this.sidebar.classList.add('resizing');
      const container = this.videoElem.closest('.annotation-player-container');
      const videoWrapper = container?.querySelector('.video-wrapper');
      if (videoWrapper) {
        videoWrapper.classList.add('no-transition');
      }

      document.body.style.cursor = 'ew-resize';
      document.body.style.userSelect = 'none';
    });

    const handleMouseMove = (e) => {
      if (!this.isResizing) return;

      const container = this.videoElem.closest('.annotation-player-container');
      if (!container) return;

      // Calculate new width (subtract delta since we're dragging from the left)
      const deltaX = this.startX - e.clientX;
      let newWidth = this.startWidth + deltaX;

      // Get constraints from CSS
      const minWidth = 200;
      const maxWidth = 600;
      const containerWidth = container.offsetWidth;

      // Ensure width is within bounds
      newWidth = Math.max(minWidth, Math.min(maxWidth, newWidth));

      // Also ensure it doesn't take up more than 70% of container
      newWidth = Math.min(newWidth, containerWidth * 0.7);

      // Apply new width
      this.width = newWidth;
      this.sidebar.style.width = `${newWidth}px`;

      // Update video wrapper margin
      this._updateVideoWrapperMargin();
    };

    const handleMouseUp = () => {
      if (!this.isResizing) return;

      this.isResizing = false;
      this.resizeHandle.classList.remove('resizing');

      // Re-enable transitions
      this.sidebar.classList.remove('resizing');
      const container = this.videoElem.closest('.annotation-player-container');
      const videoWrapper = container?.querySelector('.video-wrapper');
      if (videoWrapper) {
        videoWrapper.classList.remove('no-transition');
      }

      document.body.style.cursor = '';
      document.body.style.userSelect = '';

      // Save width to localStorage
      localStorage.setItem('subtitleSidebarWidth', this.width.toString());

      // Trigger annotation container reposition
      if (window.videoPlayer && window.videoPlayer.placeAnnotationContainer) {
        window.videoPlayer.placeAnnotationContainer();
      }
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  }

  _updateVideoWrapperMargin() {
    const container = this.videoElem.closest('.annotation-player-container');
    if (!container) return;

    const videoWrapper = container.querySelector('.video-wrapper');
    if (videoWrapper && this.visible) {
      // Check if we're on mobile (where sidebar overlays)
      if (window.innerWidth <= 425) {
        videoWrapper.style.marginRight = '0px';
      } else {
        videoWrapper.style.marginRight = `${this.width}px`;
      }
    }
  }

  /**
   * Get the currently active track from video element
   * @returns {TextTrack|null}
   */
  _getActiveTrack() {
    const tracks = Array.from(this.videoElem.textTracks);
    return tracks.find(track => track.mode === 'showing' || track.mode === 'hidden') || null;
  }

  /**
   * Called when track selection changes in the player
   * If sidebar is visible, reload cues for the new track
   */
  onTrackChanged() {
    if (this.visible) {
      this._loadCurrentTrack();
    }
  }

  /**
   * Load cues from the currently active track
   */
  _loadCurrentTrack() {
    const track = this._getActiveTrack();

    if (!track) {
      this._showEmptyState('No subtitles selected');
      return;
    }

    // If cues are already loaded, render them immediately
    if (track.cues && track.cues.length > 0) {
      this._renderCues(track);
      return;
    }

    // Otherwise, wait for cues to load
    this._showEmptyState('Loading subtitles...');

    const onLoad = () => {
      if (track.cues && track.cues.length > 0) {
        this._renderCues(track);
      } else {
        this._showEmptyState('No subtitles available');
      }
    };

    // Ensure track is in a mode that loads cues
    if (track.mode === 'disabled') {
      track.mode = 'hidden';
    }

    // Listen for load event
    track.addEventListener('load', onLoad, { once: true });

    // Fallback: check again after a delay
    setTimeout(() => {
      if (track.cues && track.cues.length > 0 && this.cueObjects.length === 0) {
        this._renderCues(track);
      }
    }, 200);
  }

  /**
   * Show empty state message
   */
  _showEmptyState(message) {
    this.cueObjects = [];
    this.content.innerHTML = `<p class="subtitle-sidebar-empty">${message}</p>`;
  }

  _renderCues(track) {
    const cues = Array.from(track.cues || []);

    if (cues.length === 0) {
      this._showEmptyState('No subtitles available');
      return;
    }

    this.content.innerHTML = '';
    this.cueObjects = [];

    cues.forEach((cue, index) => {
      const cueObj = new SubtitleSidebarCue(cue, index);
      this.cueObjects.push(cueObj);

      const cueElement = cueObj.render();

      // Seek button handler
      const seekBtn = cueElement.querySelector('.subtitle-cue-seek');
      seekBtn.addEventListener('click', () => {
        // Jump to 0.3 seconds before the cue start time
        const seekTime = Math.max(0, cue.startTime - 0.09);
        this.videoElem.currentTime = seekTime;
      });

      this.content.appendChild(cueElement);
    });

    // Update active cue immediately
    this._updateActiveCue();
  }

  _initEventListeners() {
    // Update highlighted cue as video plays
    this.videoElem.addEventListener('timeupdate', () => {
      this._updateActiveCue();
    });
  }

  _updateActiveCue() {
    if (!this.visible || this.cueObjects.length === 0) return;

    const currentTime = this.videoElem.currentTime;

    // Remove previous active class
    const previousActive = this.content.querySelector('.subtitle-cue.active');
    if (previousActive) {
      previousActive.classList.remove('active');
    }

    // Find and highlight current cue
    const activeCueObj = this.cueObjects.find(cueObj => cueObj.isActive(currentTime));

    if (activeCueObj && activeCueObj.element) {
      activeCueObj.element.classList.add('active');

      // Scroll into view if needed
      const contentRect = this.content.getBoundingClientRect();
      const cueRect = activeCueObj.element.getBoundingClientRect();

      if (cueRect.top < contentRect.top || cueRect.bottom > contentRect.bottom) {
        activeCueObj.element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }

  show() {
    // Load current track when showing sidebar
    this._loadCurrentTrack();

    this.sidebar.classList.add('visible');
    this.visible = true;

    // Add class to container to trigger video resize
    const container = this.videoElem.closest('.annotation-player-container');
    if (container) {
      container.classList.add('sidebar-open');
    }

    // Update video wrapper margin based on current width
    this._updateVideoWrapperMargin();

    // Trigger annotation container reposition after transition
    setTimeout(() => {
      if (window.videoPlayer && window.videoPlayer.placeAnnotationContainer) {
        window.videoPlayer.placeAnnotationContainer();
      }
    }, 300);
  }

  hide() {
    this.sidebar.classList.remove('visible');
    this.visible = false;

    // Remove class from container to restore video size
    const container = this.videoElem.closest('.annotation-player-container');
    if (container) {
      container.classList.remove('sidebar-open');
    }

    // Reset video wrapper margin
    const videoWrapper = container?.querySelector('.video-wrapper');
    if (videoWrapper) {
      videoWrapper.style.marginRight = '0px';
    }

    // Trigger annotation container reposition after transition
    setTimeout(() => {
      if (window.videoPlayer && window.videoPlayer.placeAnnotationContainer) {
        window.videoPlayer.placeAnnotationContainer();
      }
    }, 300);
  }

  toggle() {
    if (this.visible) {
      this.hide();
    } else {
      this.show();
    }
  }

  destroy() {
    if (this.sidebar && this.sidebar.parentNode) {
      this.sidebar.parentNode.removeChild(this.sidebar);
    }
  }
}
