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
    this.cueInstances = [];
    this.isResizing = false;
    this.startX = 0;
    this.startWidth = 0;
    this.activeTrack = null;
    this.onCueChange = null;

    // Load saved width from localStorage or use default
    this.sidebarWidth = parseInt(localStorage.getItem('subtitleSidebarWidth')) || 320;

    this._initSidebarElement();
    this._setupEventListeners();
    this._initResizeListeners();
  }

  _initSidebarElement() {
    // Create sidebar container
    this.sidebarElem = document.createElement('div');
    this.sidebarElem.className = 'subtitle-sidebar';
    this.sidebarElem.style.width = `${this.sidebarWidth}px`;

    // Create resize handle
    const sidebarResizeHandle = document.createElement('div');
    sidebarResizeHandle.className = 'subtitle-sidebar-resize-handle';
    this.sidebarResizeHandle = sidebarResizeHandle;

    // Create header
    const sidebarHeader = document.createElement('div');
    sidebarHeader.className = 'subtitle-sidebar-header';
    sidebarHeader.innerHTML = `
      <h3>Transcript</h3>
      <button class="subtitle-sidebar-close" aria-label="Close Transcript">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
          <path d="M15 5L5 15M5 5l10 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </button>
    `;

    // Create content area
    this.sidebarContent = document.createElement('div');
    this.sidebarContent.className = 'subtitle-sidebar-content';

    this.sidebarElem.appendChild(sidebarResizeHandle);
    this.sidebarElem.appendChild(sidebarHeader);
    this.sidebarElem.appendChild(this.sidebarContent);

    // Insert sidebar into player container
    const container = this.videoElem.closest('.annotation-player-container');
    if (container) {
      container.appendChild(this.sidebarElem);
      console.log('Subtitle sidebar added to container');
    } else {
      document.body.appendChild(this.sidebarElem);
      console.warn('Player container not found, appending sidebar to body');
    }

    // Close button handler
    const closeBtn = sidebarHeader.querySelector('.subtitle-sidebar-close');
    closeBtn.addEventListener('click', () => this.hide());
  }

  _initResizeListeners() {
    this.sidebarResizeHandle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      this.isResizing = true;
      this.startX = e.clientX;
      this.startWidth = this.sidebarElem.offsetWidth;
      this.sidebarResizeHandle.classList.add('resizing');

      // Disable transitions for immediate feedback
      this.sidebarElem.classList.add('resizing');
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
      this.sidebarWidth = newWidth;
      this.sidebarElem.style.width = `${newWidth}px`;

      // Update video wrapper margin
      this._updateVideoWrapperMargin();
    };

    const handleMouseUp = () => {
      if (!this.isResizing) return;

      this.isResizing = false;
      this.sidebarResizeHandle.classList.remove('resizing');

      // Re-enable transitions
      this.sidebarElem.classList.remove('resizing');
      const container = this.videoElem.closest('.annotation-player-container');
      const videoWrapper = container?.querySelector('.video-wrapper');
      if (videoWrapper) {
        videoWrapper.classList.remove('no-transition');
      }

      document.body.style.cursor = '';
      document.body.style.userSelect = '';

      // Save width to localStorage
      localStorage.setItem('subtitleSidebarWidth', this.sidebarWidth.toString());

      // Trigger annotation container reposition
      if (window.videoPlayer && window.videoPlayer.placeAnnotationBox) {
        window.videoPlayer.placeAnnotationBox();
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
        videoWrapper.style.marginRight = `${this.sidebarWidth}px`;
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
    // Remove event listener from previous track
    this._removeCueChangeListener();

    const track = this._getActiveTrack();

    // If no track is active and sidebar is visible, close it
    if (!track && this.visible) {
      this.hide();
      return;
    }

    if (this.visible) {
      this._loadActiveTrackCues();
    } else {
      // Clear current track reference even when hidden
      this.activeTrack = null;
    }
  }

  /**
   * Load cues from the currently active track
   */
  _loadActiveTrackCues() {
    const track = this._getActiveTrack();

    if (!track) {
      this._displayEmptyState('No subtitles selected');
      return;
    }

    // If cues are already loaded, render them immediately
    if (track.cues && track.cues.length > 0) {
      this._renderCueListInSidebar(track);
      this._addCueChangeListener(track);
      return;
    }

    // Otherwise, wait for cues to load
    this._displayEmptyState('Loading subtitles...');

    const onLoad = () => {
      if (track.cues && track.cues.length > 0) {
        this._renderCueListInSidebar(track);
        this._addCueChangeListener(track);
      } else {
        this._displayEmptyState('No subtitles available');
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
      if (track.cues && track.cues.length > 0 && this.cueInstances.length === 0) {
        this._renderCueListInSidebar(track);
        this._addCueChangeListener(track);
      }
    }, 200);
  }

  /**
   * Show empty state message
   */
  _displayEmptyState(message) {
    this.cueInstances = [];
    this.sidebarContent.innerHTML = `<p class="subtitle-sidebar-empty">${message}</p>`;
  }

  _renderCueListInSidebar(track) {
    const cues = Array.from(track.cues || []);

    if (cues.length === 0) {
      this._displayEmptyState('No subtitles available');
      return;
    }

    this.sidebarContent.innerHTML = '';
    this.cueInstances = [];

    cues.forEach((cue, index) => {
      const cueObj = new SubtitleSidebarCue(cue, index);
      this.cueInstances.push(cueObj);

      const cueElement = cueObj.render();

      // Seek button handler
      const seekBtn = cueElement.querySelector('.subtitle-cue-seek');
      seekBtn.addEventListener('click', () => {
        // Jump to 0.3 seconds before the cue start time
        const seekTime = Math.max(0, cue.startTime - 0.09);
        this.videoElem.currentTime = seekTime;
      });

      this.sidebarContent.appendChild(cueElement);
    });

    // Update active cue immediately
    this._highlightActiveCue();
  }

  _setupEventListeners() {
    // Event listener is now attached per-track in _addCueChangeListener
  }

  /**
   * Attach cuechange event listener to the given track
   */
  _addCueChangeListener(track) {
    // Remove any existing listener first
    this._removeCueChangeListener();

    this.activeTrack = track;
    this.onCueChange = () => {
      this._highlightActiveCue();
    };

    track.addEventListener('cuechange', this.onCueChange);

    // Also update immediately
    this._highlightActiveCue();
  }

  /**
   * Remove cuechange event listener from current track
   */
  _removeCueChangeListener() {
    if (this.activeTrack && this.onCueChange) {
      this.activeTrack.removeEventListener('cuechange', this.onCueChange);
      this.activeTrack = null;
      this.onCueChange = null;
    }
  }

  _highlightActiveCue() {
    if (!this.visible || this.cueInstances.length === 0) return;

    // Remove all previous active classes
    const previousActives = this.sidebarContent.querySelectorAll('.subtitle-cue.active');
    previousActives.forEach(el => el.classList.remove('active'));

    // Use activeCues from the track for more accurate current cue detection
    if (this.activeTrack && this.activeTrack.activeCues && this.activeTrack.activeCues.length > 0) {
      const activeCues = Array.from(this.activeTrack.activeCues);

      // Find and highlight all matching cue objects
      activeCues.forEach(activeCue => {
        const activeCueObj = this.cueInstances.find(cueObj => cueObj.cue === activeCue);

        if (activeCueObj && activeCueObj.element) {
          activeCueObj.element.classList.add('active');
        }
      });

      // Always scroll the first active cue into view, aiming for ~25% from the top
      const firstActiveCueObj = this.cueInstances.find(cueObj => cueObj.cue === activeCues[0]);
      if (firstActiveCueObj && firstActiveCueObj.element) {
        const contentHeight = this.sidebarContent.clientHeight;
        const cueOffsetTop = firstActiveCueObj.element.offsetTop;
        const targetScrollTop = cueOffsetTop - contentHeight * 0.25;

        this.sidebarContent.scrollTo({
          top: targetScrollTop,
          behavior: 'smooth'
        });
      }
    }
  }

  show() {
    // Load current track when showing sidebar
    this._loadActiveTrackCues();

    this.sidebarElem.classList.add('visible');
    this.visible = true;

    // Add class to container to trigger video resize
    const container = this.videoElem.closest('.annotation-player-container');
    if (container) {
      container.classList.add('subtitle-sidebar-open');
    }

    // Update video wrapper margin based on current width
    this._updateVideoWrapperMargin();

    // Trigger annotation container reposition after transition
    setTimeout(() => {
      if (window.videoPlayer && window.videoPlayer.placeAnnotationBox) {
        window.videoPlayer.placeAnnotationBox();
      }
    }, 300);
  }

  hide() {
    // Keep event listener attached - don't detach it

    this.sidebarElem.classList.remove('visible');
    this.visible = false;

    // Remove class from container to restore video size
    const container = this.videoElem.closest('.annotation-player-container');
    if (container) {
      container.classList.remove('subtitle-sidebar-open');
    }

    // Reset video wrapper margin
    const videoWrapper = container?.querySelector('.video-wrapper');
    if (videoWrapper) {
      videoWrapper.style.marginRight = '0px';
    }

    // Trigger annotation container reposition after transition
    setTimeout(() => {
      if (window.videoPlayer && window.videoPlayer.placeAnnotationBox) {
        window.videoPlayer.placeAnnotationBox();
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
    // Clean up event listener
    this._removeCueChangeListener();

    if (this.sidebarElem && this.sidebarElem.parentNode) {
      this.sidebarElem.parentNode.removeChild(this.sidebarElem);
    }
  }
}
