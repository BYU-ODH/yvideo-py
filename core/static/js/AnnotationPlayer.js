import { SubtitleSidebar } from "./SubtitleSidebar.js";

export class AnnotationPlayer {
  constructor(options = {}) {
    this.container = this._getElement(options.container);
    if (!this.container) {
      throw new Error('AnnotationPlayer requires a container element');
    }
    if (
      this.container.id ||
      !this.container.classList.contains('annotation-player-container')
    ) {
      throw new Error(
       'AnnotationPlayer container must have no id and must have the "annotation-player-container" class.'
      );
    }

    this.videoElem = this._getElement(options.video) || this.container.querySelector('video');
    if (!this.videoElem) {
      this.videoElem = document.createElement('video');
    }

    let videoWrapper = this.videoElem.closest('.video-wrapper');
    if (!videoWrapper) {
      videoWrapper = document.createElement('div');
      videoWrapper.className = 'video-wrapper';
      if (this.videoElem.parentNode === this.container) {
        this.container.insertBefore(videoWrapper, this.videoElem);
      } else {
        this.container.appendChild(videoWrapper);
      }
      videoWrapper.appendChild(this.videoElem);
    }
    this.videoWrapper = videoWrapper;

    this.annotationBox = this._getElement(options.annotationBox) ||
                               this.videoWrapper.querySelector('.annotation-box');
    if (!this.annotationBox) {
      this.annotationBox = document.createElement('div');
      this.annotationBox.className = 'annotation-box';
      this.videoWrapper.appendChild(this.annotationBox);
    }

    if (!this.annotationBox.querySelector('.bezel-icon')) {
      const bezelIcon = document.createElement('div');
      bezelIcon.className = 'bezel-icon';
      this.annotationBox.appendChild(bezelIcon);
    }
    if (!this.annotationBox.querySelector('.bezel-text')) {
      const bezelText = document.createElement('div');
      bezelText.className = 'bezel-text';
      this.annotationBox.appendChild(bezelText);
    }

    this.disabledControls = options.disabledControls || [];
    this._createControls();
    this.videoElem.controls = false;

    this.playbackRates = options.playbackRates || [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0];
    this.state = {
      playing: false,
      started: false,
      currentTime: 0,
      playbackRate: 1.0,
      volume: 1.0,
      muted: false,
      fullscreen: false,
      mouseInactive: false,
      hovering: false,
      controlsHovering: false,
    };

    if (this.controls.volumeBtn) {
      this._updateVolumeIcon();
    }

    this.annotations = [];
    this.subtitleTrackBlobUrls = []; // Store subtitle track blob URLs for cleanup
    this.currently = { muting: -1, blanking: -1, blurring: -1 };

    this.mouseTimer = null;
    this.controlsTimeout = null;
    this.timeCache = 0;
    this.isDragging = false;
    this.wasPlayingBeforeDrag = false;
    this.draggingRAF = null; // Track requestAnimationFrame for dragging

    if (options.subtitleTracks && Array.isArray(options.subtitleTracks)) {
      this._loadSubtitleTracks(options.subtitleTracks);
    }

    this.subtitleSidebar = null;
    this._enableSubtitleSidebar = options.subtitleSidebar === true;

    this.clips = options.clips || [];

    this.setupEventListeners();
  }

  static icons = {
    playPauseBtn: {
      play: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><path d="M 12,26 18.5,22 18.5,14 12,10 z M 18.5,22 25,18 25,18 18.5,14 z"></path></svg>`,
      pause: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><path d="M 12,26 16,26 16,10 12,10 z M 21,26 25,26 25,10 21,10 z"></path></svg>`,
      replay: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><path d="M 18,11 V 7 l -5,5 5,5 v -4 c 3.3,0 6,2.7 6,6 0,3.3 -2.7,6 -6,6 -3.3,0 -6,-2.7 -6,-6 H 10 c 0,4.4 3.6,8 8,8 4.4,0 8,-3.6 8,-8 0,-4.4 -3.6,-8 -8,-8 z"></path></svg>`
    },
    volume: {
      mute: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><path d="m 21.48,17.98 c 0,-1.77 -1.02,-3.29 -2.5,-4.03 v 2.21 l 2.45,2.45 c .03,-0.2 .05,-0.41 .05,-0.63 z m 2.5,0 c 0,.94 -0.2,1.82 -0.54,2.64 l 1.51,1.51 c .66,-1.24 1.03,-2.65 1.03,-4.15 0,-4.28 -2.99,-7.86 -7,-8.76 v 2.05 c 2.89,.86 5,3.54 5,6.71 z M 9.25,8.98 l -1.27,1.26 4.72,4.73 H 7.98 v 6 H 11.98 l 5,5 v -6.73 l 4.25,4.25 c -0.67,.52 -1.42,.93 -2.25,1.18 v 2.06 c 1.38,-0.31 2.63,-0.95 3.69,-1.81 l 2.04,2.05 1.27,-1.27 -9,-9 -7.72,-7.72 z m 7.72,.99 -2.09,2.08 2.09,2.09 V 9.98 z"></path></svg>`,
      low: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><path d="M8,21 L12,21 L17,26 L17,10 L12,15 L8,15 L8,21 Z M19,14 L19,22 C20.48,21.32 21.5,19.77 21.5,18 C21.5,16.26 20.48,14.74 19,14 Z"></path></svg>`,
      medium: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><path d="M8,21 L12,21 L17,26 L17,10 L12,15 L8,15 L8,21 Z M19,14 L19,22 C20.48,21.32 21.5,19.77 21.5,18 C21.5,16.26 20.48,14.74 19,14 Z"></path></svg>`,
      high: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><path d="M8,21 L12,21 L17,26 L17,10 L12,15 L8,15 L8,21 Z M19,14 L19,22 C20.48,21.32 21.5,19.77 21.5,18 C21.5,16.26 20.48,14.74 19,14 ZM19,11.29 C21.89,12.15 24,14.83 24,18 C24,21.17 21.89,23.85 19,24.71 L19,26.77 C23.01,25.86 26,22.28 26,18 C26,13.72 23.01,10.14 19,9.23 L19,11.29 Z"></path></svg>`
    },
    speed: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><path d="M 10,24 18.5,18 10,12 V 24 z M 19,12 V 24 L 27.5,18 19,12 z"></path></svg>`,
    speedLeft: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><path d="M 26,24 17.5,18 26,12 V 24 z M 17,12 V 24 L 8.5,18 17,12 z"></path></svg>`,
    captionsBtn: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><path d="M11,11 C9.89,11 9,11.9 9,13 L9,23 C9,24.1 9.89,25 11,25 L25,25 C26.1,25 27,24.1 27,23 L27,13 C27,11.9 26.1,11 25,11 L11,11 Z M17,17 L15.5,17 L15.5,16.5 L13.5,16.5 L13.5,19.5 L15.5,19.5 L15.5,19 L17,19 L17,20 C17,20.55 16.55,21 16,21 L13,21 C12.45,21 12,20.55 12,20 L12,16 C12,15.45 12.45,15 13,15 L16,15 C16.55,15 17,15.45 17,16 L17,17 L17,17 Z M24,17 L22.5,17 L22.5,16.5 L20.5,16.5 L20.5,19.5 L22.5,19.5 L22.5,19 L24,19 L24,20 C24,20.55 23.55,21 23,21 L20,21 C19.45,21 19,20.55 19,20 L19,16 C19,15.45 19.45,15 20,15 L23,15 C23.55,15 24,15.45 24,16 L24,17 L24,17 Z"></path></svg>`,
    subtitleSidebarBtn: `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 8h8M8 12h8M8 16h5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
    clipsBtn: `<svg viewBox="0 0 24 24" fill="currentColor" width="100%" height="100%"><path d="M9.64,7.64c0.23-0.5,0.36-1.05,0.36-1.64c0-2.21-1.79-4-4-4S2,3.79,2,6s1.79,4,4,4c0.59,0,1.14-0.13,1.64-0.36L10,12l-2.36,2.36 C7.14,14.13,6.59,14,6,14c-2.21,0-4,1.79-4,4s1.79,4,4,4s4-1.79,4-4c0-0.59-0.13-1.14-0.36-1.64L12,14l7,7h3v-1L9.64,7.64z M6,8 C4.9,8,4,7.11,4,6s0.9-2,2-2s2,0.89,2,2S7.1,8,6,8z M6,20c-1.1,0-2-0.89-2-2s0.9-2,2-2s2,0.89,2,2S7.1,20,6,20z M12,12.5 c-0.28,0-0.5-0.22-0.5-0.5s0.22-0.5,0.5-0.5s0.5,0.22,0.5,0.5S12.28,12.5,12,12.5z M19,3l-6,6l2,2l7-7V3H19z"/></svg>`,
    fullscreenBtn: {
      enter: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><g><path d="m 10,16 2,0 0,-4 4,0 0,-2 L 10,10 l 0,6 0,0 z"></path></g><g><path d="m 20,10 0,2 4,0 0,4 2,0 L 26,10 l -6,0 0,0 z"></path></g><g><path d="m 24,24 -4,0 0,2 L 26,26 l 0,-6 -2,0 0,4 0,0 z"></path></g><g><path d="M 12,20 10,20 10,26 l 6,0 0,-2 -4,0 0,-4 0,0 z"></path></g></svg>`,
      exit: `<svg height="100%" version="1.1" viewBox="0 0 36 36" width="100%"><g><path d="m 14,14 -4,0 0,2 6,0 0,-6 -2,0 0,4 0,0 z"></path></g><g><path d="m 22,14 0,-4 -2,0 0,6 6,0 0,-2 -4,0 0,0 z"></path></g><g><path d="m 20,26 2,0 0,-4 4,0 0,-2 -6,0 0,6 0,0 z"></path></g><g><path d="m 10,22 4,0 0,4 2,0 0,-6 -6,0 0,2 0,0 z"></path></g></svg>`
    }
  };

  setAspectRatio() {
    this.videoHeight = this.videoElem.videoHeight;
    this.videoWidth = this.videoElem.videoWidth;
    this.aspectRatio;
    if (this.videoHeight == 0) {
      this.aspectRatio = 0;
    } else {
      this.aspectRatio = this.videoWidth / this.videoHeight;
    }
  }

  setVidWrapperToWide() {
    this.videoWrapper.classList.remove("full-height");
  }

  setVidWrapperToTall() {
    this.videoWrapper.classList.add("full-height");
  }

  setVideoWrapperStyling() {
    const containerDim = this.container.getBoundingClientRect();
    const containerAspectRatio = containerDim.width / containerDim.height;

    if (containerAspectRatio > this.aspectRatio) {
      this.setVidWrapperToTall();
    }
    else {
      this.setVidWrapperToWide();
    }
  }

  _getElement(selector) {
    if (!selector) return null;
    if (selector instanceof HTMLElement) return selector;
    if (typeof selector === 'string') return document.querySelector(selector);
    return null;
  }

  _createControls() {
    this.controls = {};

    const controlBarHTML = `
      <div class="video-controls">
        <div class="scrubber">
          <div class="scrubber-buffered"></div>
          <div class="scrubber-progress"></div>
          <div class="scrubber-dot"></div>
        </div>
        <div class="bottom-controls">
          <div class="left-controls">
            <button class="play-pause-btn" aria-label="Play/Pause"></button>
            <div class="volume-controls">
              <button class="volume-btn" aria-label="Mute/Unmute"></button>
              <input type="range" class="volume-slider" min="0" max="1" step="0.1" value="1">
            </div>
            <div class="play-time">0:00 / 0:00</div>
          </div>
          <div class="right-controls">
            <div class="speed-btn-wrapper" style="position:relative;display:inline-block;">
              <button class="speed-btn" aria-label="Playback Speed"><span class="speed-text">1x</span></button>
              <div class="speed-menu" style="display:none;position:absolute;bottom:100%;right:0;z-index:100;background:#222;color:#fff;border-radius:4px;box-shadow:0 2px 8px rgba(0,0,0,0.2);padding:4px 0;min-width:60px;"></div>
            </div>
            <div class="clips-btn-wrapper" style="position:relative;display:inline-block;display:none;">
              <button class="clips-btn" aria-label="Clips"></button>
              <div class="clips-menu" style="display:none;position:absolute;bottom:100%;right:0;z-index:100;background:#222;color:#fff;border-radius:4px;box-shadow:0 2px 8px rgba(0,0,0,0.2);padding:4px 0;min-width:150px;"></div>
            </div>
            <div class="captions-btn-wrapper" style="position:relative;display:inline-block;display:none;">
              <button class="captions-btn" aria-label="Captions"></button>
              <div class="captions-menu" style="display:none;position:absolute;bottom:100%;right:0;z-index:100;background:#222;color:#fff;border-radius:4px;box-shadow:0 2px 8px rgba(0,0,0,0.2);padding:4px 0;min-width:100px;"></div>
            </div>
            <button class="subtitle-sidebar-btn" aria-label="Subtitle sidebar" style="display:none;"></button>
            <button class="fullscreen-btn" aria-label="Fullscreen"></button>
          </div>
        </div>
      </div>
    `;

    const tempControlsDiv = document.createElement('div');
    tempControlsDiv.innerHTML = controlBarHTML;
    while (tempControlsDiv.firstChild) {
      this.videoWrapper.appendChild(tempControlsDiv.firstChild);
    }

    // Store references to controls (skip disabled ones)
    if (!this.disabledControls.includes('playPauseBtn')) {
      this.controls.playPauseBtn = this.videoWrapper.querySelector('.play-pause-btn');
      this.controls.playPauseBtn.innerHTML = AnnotationPlayer.icons.playPauseBtn.play;
    }
    if (!this.disabledControls.includes('volume')) {
        this.controls.volumeBtn = this.videoWrapper.querySelector('.volume-btn');
        this.controls.volumeSlider = this.videoWrapper.querySelector('.volume-slider');
    }
    if (!this.disabledControls.includes('scrubber')) {
      this.controls.scrubber = this.videoWrapper.querySelector('.scrubber');
      this.controls.scrubberBuffered = this.videoWrapper.querySelector('.scrubber-buffered');
      this.controls.scrubberProgress = this.videoWrapper.querySelector('.scrubber-progress');
      this.controls.scrubberDot = this.videoWrapper.querySelector('.scrubber-dot');
    }
    if (!this.disabledControls.includes('playTime')) {
      this.controls.playTime = this.videoWrapper.querySelector('.play-time');
    }
    if (!this.disabledControls.includes('speedBtn')) {
      this.controls.speedBtnWrapper = this.videoWrapper.querySelector('.speed-btn-wrapper');
      this.controls.speedBtn = this.videoWrapper.querySelector('.speed-btn');
      this.controls.speedMenu = this.videoWrapper.querySelector('.speed-menu');
    }
    if (!this.disabledControls.includes('clipsBtn')) {
      this.controls.clipsBtnWrapper = this.videoWrapper.querySelector('.clips-btn-wrapper');
      this.controls.clipsBtn = this.videoWrapper.querySelector('.clips-btn');
      this.controls.clipsMenu = this.videoWrapper.querySelector('.clips-menu');
      this.controls.clipsBtn.innerHTML = AnnotationPlayer.icons.clipsBtn;
    }
    if (!this.disabledControls.includes('captionsBtn')) {
      this.controls.captionsBtnWrapper = this.videoWrapper.querySelector('.captions-btn-wrapper');
      this.controls.captionsBtn = this.videoWrapper.querySelector('.captions-btn');
      this.controls.captionsMenu = this.videoWrapper.querySelector('.captions-menu');
      this.controls.captionsBtn.innerHTML = AnnotationPlayer.icons.captionsBtn;
    }
    if (!this.disabledControls.includes('subtitleSidebarBtn')) {
      this.controls.subtitleSidebarBtn = this.videoWrapper.querySelector('.subtitle-sidebar-btn');
      this.controls.subtitleSidebarBtn.innerHTML = AnnotationPlayer.icons.subtitleSidebarBtn;
    }
    if (!this.disabledControls.includes('fullscreenBtn')) {
      this.controls.fullscreenBtn = this.videoWrapper.querySelector('.fullscreen-btn');
      this.controls.fullscreenBtn.innerHTML = AnnotationPlayer.icons.fullscreenBtn.enter;
    }

    this.bezelIcon = this.annotationBox.querySelector('.bezel-icon');
    this.bezelText = this.annotationBox.querySelector('.bezel-text');

    this.disabledControls.forEach(controlName => {
      const classMap = {
        'playPauseBtn': '.play-pause-btn',
        'volume': '.volume-controls',
        'scrubber': '.scrubber',
        'playTime': '.play-time',
        'speedBtn': '.speed-btn-wrapper',
        'clipsBtn': '.clips-btn-wrapper',
        'captionsBtn': '.captions-btn-wrapper',
        'subtitleSidebarBtn': '.subtitle-sidebar-btn',
        'fullscreenBtn': '.fullscreen-btn',
      };
      const selector = classMap[controlName];
      if (selector) {
        const element = this.videoWrapper.querySelector(selector);
        if (element) element.remove();
      }
    });

    // Set container reference for fullscreen and other operations
    this.controls.container = this.container;
  }

  _conditionallyUpdateControlsIconVisibility() {
    if (this.controls.captionsBtnWrapper && !this.disabledControls.includes('captionsBtn')) {
      const hasTracks = this.videoElem.textTracks.length > 0;
      this.controls.captionsBtnWrapper.style.display = hasTracks ? 'inline-block' : 'none';
    }

    if (this.controls.subtitleSidebarBtn && !this.disabledControls.includes('subtitleSidebarBtn')) {
      this.controls.subtitleSidebarBtn.style.display = this._enableSubtitleSidebar ? 'inline-block' : 'none';
      this._updateSubtitleSidebarButtonDisplay();
    }

    if (this.controls.clipsBtnWrapper && !this.disabledControls.includes('clipsBtn')) {
      const hasClips = this.clips && this.clips.length > 0;
      this.controls.clipsBtnWrapper.style.display = hasClips ? 'inline-block' : 'none';
      if (hasClips) {
        this._renderClipsMenu();
      }
    }
  }

  _updateSubtitleSidebarButtonDisplay() {
    if (!this.controls.subtitleSidebarBtn) return;

    const hasActiveTrack = this._getActiveTrack() !== null;

    if (hasActiveTrack) {
      this.controls.subtitleSidebarBtn.classList.remove('inactive');
      this.controls.subtitleSidebarBtn.disabled = false;
      this.controls.subtitleSidebarBtn.style.cursor = 'pointer';
    } else {
      this.controls.subtitleSidebarBtn.classList.add('inactive');
      this.controls.subtitleSidebarBtn.disabled = true;
      this.controls.subtitleSidebarBtn.style.cursor = 'not-allowed';
    }
  }

  _getActiveTrack() {
    const subtitleTracks = Array.from(this.videoElem.textTracks);
    return subtitleTracks.find(subtitleTrack => subtitleTrack.mode === 'showing' || subtitleTrack.mode === 'hidden') || null;
  }

  _getActiveTrackIndex() {
    const subtitleTracks = Array.from(this.videoElem.textTracks);
    return subtitleTracks.findIndex(subtitleTrack => subtitleTrack.mode === 'showing' || subtitleTrack.mode === 'hidden');
  }

  _showBezel(icon, text) {
    if (this.bezelTimeout) {
      clearTimeout(this.bezelTimeout);
    }

    if (icon && this.bezelIcon) {
      this.bezelIcon.innerHTML = icon;
      this.bezelIcon.classList.add('show');
    }

    if (text && this.bezelText) {
      this.bezelText.textContent = text;
      this.bezelText.classList.add('show');
    }

    this.bezelTimeout = setTimeout(() => {
      if (this.bezelIcon) this.bezelIcon.classList.remove('show');
      if (this.bezelText) this.bezelText.classList.remove('show');
    }, 800);
  }

  parseHummediaAnnotations(annotationObj) {
    const annotations = [];
    const innerObj = annotationObj["media"][0]["tracks"][0]["trackEvents"];
    for (const humAnno of innerObj) {
      let annotation = {
        label: humAnno.popcornOptions["label"],
        start: humAnno.popcornOptions["start"],
        end: humAnno.popcornOptions["end"],
        details: humAnno.popcornOptions["details"],
        type: humAnno["type"],
      };
      annotations.push(annotation);
    }
    return annotations;
  }

  parseICLegacyAnnotations(annotationObj) {
    const annotations = [];
    for (const icAnno of annotationObj) {
      let annotation = {
        label: icAnno.options["label"],
        start: icAnno.options["start"],
        end: icAnno.options["end"],
        type: icAnno.options["type"],
        details: icAnno.options["details"],
      };
      if (annotation.type === "censor" && annotation.details.interpolate) {
        this.interpolateCensor(annotation);
      }
      annotations.push(annotation);
    }
    return annotations;
  }

  parseYvideoV1Annotations(annotationObj) {
    // YVideo v1 (old React app) annotations are typically an array of objects like:
    // { type: "mute", start: 12.5, end: 15.2, label: "Mute", details: { ... } }
    // Sometimes details may be missing or minimal.
    const annotations = [];
    for (const anno of annotationObj) {
      annotations.push({
        label: anno.label || "",
        start: anno.start,
        end: anno.end,
        type: anno.type,
        details: anno.details || {},
      });
    }
    return annotations;
  }

  loadData(data) {
    if (!data.annotations && Array.isArray(data)) {
      this.annotations = data;
    } else {
      this.annotations = data.annotations || [];
      this.clips = data.clips || this.clips;

      if (data.subtitleTracks && Array.isArray(data.subtitleTracks)) {
        this._loadSubtitleTracks(data.subtitleTracks);
      }
    }

    if (this.annotations.length > 0) {
      const firstAnnotation = this.annotations[0];
      if (firstAnnotation.options) {
        this.annotations = this.parseICLegacyAnnotations(this.annotations);
      } else if (firstAnnotation.type && firstAnnotation.start !== undefined) {
        this.annotations = this.parseYvideoV1Annotations(this.annotations);
      } else if (firstAnnotation.media) {
        this.annotations = this.parseHummediaAnnotations(this.annotations);
      } else {
        console.warn("Unknown annotation format:", firstAnnotation);
        this.annotations = [];
      }
    }
    this.setupVideoElemAnnotations();
    this.renderSkipsOnScrubber();

    this.videoElem.addEventListener('loadedmetadata', () => {
      this.renderSkipsOnScrubber();
    });
  }

  renderSkipsOnScrubber() {
    if (!this.controls.scrubber || !this.annotations || !this.videoElem.duration) return;

    this.controls.scrubber.querySelectorAll('.skip-on-scrubber').forEach(el => el.remove());

    const skipEvents = this.annotations.filter(event =>
      event.type === 'Skip' || event.type === 'skip'
    );

    skipEvents.forEach(event => {
      const startPercent = (parseFloat(event.start) / this.videoElem.duration) * 100;
      const endPercent = (parseFloat(event.end) / this.videoElem.duration) * 100;

      const skipElement = document.createElement('div');
      skipElement.className = 'skip-on-scrubber';
      skipElement.style.left = `${startPercent}%`;
      skipElement.style.width = `${endPercent - startPercent}%`;

      this.controls.scrubber.appendChild(skipElement);
    });
  }

  renderActiveClipOnScrubber() {
    if (!this.controls.scrubber || !this.clips || !this.videoElem.duration) return;

    this.controls.scrubber.querySelectorAll('.clip-on-scrubber').forEach(el => el.remove());

    if (this.activeClipIndex !== null && this.clips[this.activeClipIndex]) {
      const clip = this.clips[this.activeClipIndex];
      const startPercent = (parseFloat(clip.start) / this.videoElem.duration) * 100;
      const endPercent = (parseFloat(clip.end) / this.videoElem.duration) * 100;

      const clipElement = document.createElement('div');
      clipElement.className = 'clip-on-scrubber active';
      clipElement.dataset.clipIndex = this.activeClipIndex;
      clipElement.style.left = `${startPercent}%`;
      clipElement.style.width = `${endPercent - startPercent}%`;

      this.controls.scrubber.appendChild(clipElement);
    }
  }

  _renderClipsMenu() {
    if (!this.controls.clipsMenu) return;

    let menuHTML = '<div class="clip-option" data-clip="off" style="padding:8px 16px;cursor:pointer;white-space:nowrap;">None</div>';

    this.clips.forEach((clip, index) => {
      const label = clip.label || `Clip ${index + 1}`;
      menuHTML += `<div class="clip-option" data-clip="${index}" style="padding:8px 16px;cursor:pointer;white-space:nowrap;">${label}</div>`;
    });

    this.controls.clipsMenu.innerHTML = menuHTML;

    this._updateClipsMenuHighlight();
  }

  _updateClipsMenuHighlight() {
    if (!this.controls.clipsMenu) return;

    this.controls.clipsMenu.querySelectorAll('.clip-option').forEach(option => {
      option.classList.remove('active-value');
    });

    if (this.activeClipIndex !== null) {
      const activeOption = this.controls.clipsMenu.querySelector(`[data-clip="${this.activeClipIndex}"]`);
      if (activeOption) {
        activeOption.classList.add('active-value');
      }
    } else {
      const offOption = this.controls.clipsMenu.querySelector('[data-clip="off"]');
      if (offOption) {
        offOption.classList.add('active-value');
      }
    }
  }

  setActiveClip(clipIndex) {
    if (clipIndex === 'off') {
      this.activeClipIndex = null;
      this._updateClipHighlighting();
      this._updateClipsMenuHighlight();
      return;
    }

    const index = parseInt(clipIndex);
    if (index < 0 || index >= this.clips.length) return;

    this.activeClipIndex = index;
    const clip = this.clips[index];

    this.skipTo(clip.start);
    if (!this.videoElem.paused) {
      this.pause();
    }

    this._updateClipHighlighting();
    this._updateClipsMenuHighlight();
  }

  _updateClipHighlighting() {
    if (this.controls.clipsBtn) {
      if (this.activeClipIndex !== null) {
        this.controls.clipsBtn.classList.add('clip-active');
      } else {
        this.controls.clipsBtn.classList.remove('clip-active');
      }
    }
    this.renderActiveClipOnScrubber();
  }

  play() {
    this.videoElem.play();
    this.paused = false;
    this.state.playing = true;
    this.state.started = true;
  }

  pause() {
    this.videoElem.pause();
    this.paused = true;
    this.state.playing = false;
    if (this.container) {
      this.container.classList.remove("controls-hidden");
    }
  }

  togglePlayPause() {
    if (this.videoElem.paused) {
      this.play();
    } else {
      this.pause();
    }
    this._updatePlayPauseIcon();
  }

  _updatePlayPauseIcon() {
    if (!this.controls.playPauseBtn) return;

    if (this.state.playing) {
      this.controls.playPauseBtn.innerHTML = AnnotationPlayer.icons.playPauseBtn.pause;
    } else {
      this.controls.playPauseBtn.innerHTML = AnnotationPlayer.icons.playPauseBtn.play;
    }
  }

  skipTo(time) {
    this.videoElem.currentTime = time;
    this.timeCache = time;
    this.applyAnnotations();
  }

  setVideoSource(src) {
    this.videoElem.src = src;
  }

  getCurrentTime() {
    return this.videoElem.currentTime;
  }

  setCurrentTime(time) {
    this.skipTo(time);
  }

  isPaused() {
    return this.videoElem.paused;
  }

  setupVideoElemAnnotations() {
    this._onPlaying = () => this.applyAnnotations();
    this.currently = { muting: -1, blanking: -1, blurring: -1 };
    this.videoElem.addEventListener("playing", this._onPlaying);
  }

  applyAnnotations() {
    if (!this.annotations) return;
    let time = this.videoElem.currentTime;
    this.timeCache = time;
    this.state.currentTime = time;

    // Check if we've passed the end of the selected clip
    if (this.activeClipIndex !== null && this.clips && this.clips[this.activeClipIndex]) {
      const clip = this.clips[this.activeClipIndex];
      if (time >= clip.end) {
        // Auto-deselect clip and pause
        this.activeClipIndex = null;
        this._updateClipHighlighting();
        this._updateClipsMenuHighlight();
        this.pause();
      }
    }

    let numAnnotations = this.annotations.length;
    let isMuteAnnotationActive = false;
    for (let i = 0; i < numAnnotations; i++) {
      let vMuted = this.videoElem.muted;
      let vBlanked = this.videoElem.classList.contains("blanked");
      let vBlurred = this.videoElem.classList.contains("blurred");
      let a = this.annotations[i];
      let aStart = a["start"];
      let aEnd = a["end"];
      let aType = a["type"];
      let aDetails = a["details"];
      switch (aType) {
        case "skip":
          if (time >= aStart && time < aEnd && !this.paused) {
            this.skipTo(aEnd);
          }
          break;
        case "mute":
        case "mutePlugin":
          if (this.currently.muting === -1 || this.currently.muting === i) {
            if (time >= aStart && time < aEnd) {
              isMuteAnnotationActive = true;
              if (!vMuted) {
                this.currently.muting = i;
                this.mute(true); // pass true for annotation mute
              }
            } else {
              if (vMuted) {
                this.currently.muting = -1;
                this.unmute(true); // pass true for annotation mute
              }
            }
          }
          break;
        case "blank":
          if (this.currently.blanking === -1 || this.currently.blanking === i) {
            if (time >= aStart && time < aEnd) {
              if (!vBlanked) {
                this.currently.blanking = i;
                this.blank();
              }
            } else {
              if (vBlanked) {
                this.currently.blanking = -1;
                this.unblank();
              }
            }
          }
          break;
        case "blur":
          if (this.currently.blurring === -1 || this.currently.blurring === i) {
            if (time >= aStart && time < aEnd) {
              if (!vBlurred) {
                this.currently.blurring = i;
                this.blur();
              }
            } else {
              if (vBlurred) {
                this.currently.blurring = -1;
                this.unblur();
              }
            }
          }
          break;
        case "censor":
          if (time >= aStart && time < aEnd) {
            if (!this.annotationBox.querySelector("#censor" + i)) {
              const censor = document.createElement("div");
              censor.id = "censor" + i;
              censor.className = "censor " + aDetails["type"];
              censor.style.position = "absolute";
              censor.style.width = aDetails["position"][aStart][2] + "%";
              censor.style.height = aDetails["position"][aStart][3] + "%";
              censor.style.left = aDetails["position"][aStart][0] + "%";
              censor.style.top = aDetails["position"][aStart][1] + "%";
              if (aDetails["type"] === "black" || aDetails["type"] === "red") {
                censor.style.backgroundColor = aDetails["type"];
              } else if (aDetails["type"] === "blur") {
                censor.style.backdropFilter =
                  "blur(" + aDetails["amount"] + ")";
              }
              this.annotationBox.appendChild(censor);
            } else {
              const censor = this.annotationBox.querySelector(
                "#censor" + i,
              );
              let annoTime;
              if (a.details.interpolate) {
                annoTime = Object.keys(a.details.intPositions).reduce(
                  (prev, curr) =>
                    Math.abs(curr - time) < Math.abs(prev - time) ? curr : prev,
                );
                censor.style.left = aDetails["intPositions"][annoTime][0] + "%";
                censor.style.top = aDetails["intPositions"][annoTime][1] + "%";
                if (
                  aDetails["intPositions"][annoTime][2] &&
                  aDetails["intPositions"][annoTime][3]
                ) {
                  censor.style.width =
                    aDetails["intPositions"][annoTime][2] + "%";
                  censor.style.height =
                    aDetails["intPositions"][annoTime][3] + "%";
                }
              } else {
                annoTime = Object.keys(a.details.position).reduce(
                  (prev, curr) =>
                    Math.abs(curr - time) < Math.abs(prev - time) ? curr : prev,
                );
                censor.style.left = aDetails["position"][annoTime][0] + "%";
                censor.style.top = aDetails["position"][annoTime][1] + "%";
                if (
                  aDetails["position"][annoTime][2] &&
                  aDetails["position"][annoTime][3]
                ) {
                  censor.style.width = aDetails["position"][annoTime][2] + "%";
                  censor.style.height = aDetails["position"][annoTime][3] + "%";
                }
              }
            }
          } else {
            const existingCensor = this.annotationBox.querySelector(
              "#censor" + i,
            );
            if (existingCensor) {
              existingCensor.remove();
            }
          }
          break;
      }
    }
    this.isMuteAnnotationActive = isMuteAnnotationActive;
    this._updateVolumeControlsDisplay();

    if (this.videoElem.paused) return;
    requestAnimationFrame(() => this.applyAnnotations());
  }

  resetAnnotations() {
    this.videoElem.removeEventListener("playing", this._onPlaying);
    this.videoElem.classList.remove("blanked");
    this.videoElem.classList.remove("blurred");
    Array.from(
      this.annotationBox.querySelectorAll("[id^=censor]"),
    ).forEach((el) => el.remove());
    this.unmute();
  }

  blank() {
    this.videoElem.classList.add("blanked");
    // TODO Optionally add style for blanked video
  }

  unblank() {
    this.videoElem.classList.remove("blanked");
  }

  blur() {  // Blur the whole screen (not just censored areas)
    this.videoElem.classList.add("blurred");
    // TODO Make this subtype of `blanked` with CSS options
  }

  unblur() {
    this.videoElem.classList.remove("blurred");
  }

  mute(isAnnotation = false) {
    if (!isAnnotation) {
      this.previousVolume = this.state.volume;
    }
    this.videoElem.muted = true;
    this.state.muted = true;
    if (this.controls.volumeSlider) {
      this.controls.volumeSlider.value = 0;
    }
    this._updateVolumeIcon();
  }

  unmute(isAnnotation = false) {
    this.videoElem.muted = false;
    this.state.muted = false;
    if (this.controls.volumeSlider) {
      // Restore previous volume only if not annotation mute
      this.controls.volumeSlider.value = isAnnotation ? this.previousVolume : this.state.volume;
    }
    this._updateVolumeIcon();
    if (!isAnnotation && this.previousVolume !== undefined) {
      this.setVolume(this.previousVolume);
    }
  }

  toggleMute() {
    if (this.isMuteAnnotationActive) return;

    if (this.videoElem.muted) {
      this.unmute();
      if (this.state.volume < 0.05) {
        this.setVolume(0.05);
      }
    } else {
      this.mute();
    }
  }

  setVolume(value) {
    if (this.isMuteAnnotationActive) return;

    // Quantize to nearest 0.1 step
    let volume = Math.round(Math.max(0, Math.min(1, value)) * 10) / 10;
    this.state.volume = volume;
    this.videoElem.volume = volume;
    if (volume > 0) {
      this.state.muted = false;
      this.videoElem.muted = false;
    } else {
      this.state.muted = true;
      this.videoElem.muted = true;
    }
    this._updateVolumeIcon();
    if (this.controls.volumeSlider) {
      this.controls.volumeSlider.value = volume;
    }
  }

  _getVolumeIcon() {
    if (this.state.muted || this.state.volume === 0) {
      return AnnotationPlayer.icons.volume.mute;
    }
    if (this.state.volume < 0.5) {
      return AnnotationPlayer.icons.volume.low;
    }
    return AnnotationPlayer.icons.volume.high;
  }

  _updateVolumeIcon() {
    if (!this.controls.volumeBtn) return;
    this.controls.volumeBtn.innerHTML = this._getVolumeIcon();
  }

  _updateVolumeControlsDisplay() {
    if (this.controls.volumeBtn) {
      if (this.isMuteAnnotationActive) {
        this.controls.volumeBtn.classList.add('inactive');
      } else {
        this.controls.volumeBtn.classList.remove('inactive');
      }
    }
    if (this.controls.volumeSlider) {
      if (this.isMuteAnnotationActive) {
        this.controls.volumeSlider.classList.add('inactive');
        this.controls.volumeSlider.disabled = true;
      } else {
        this.controls.volumeSlider.classList.remove('inactive');
        this.controls.volumeSlider.disabled = false;
      }
    }
  }

  interpolateCensor(annotation) {
    annotation.details["intPositions"] = {};
    let position = annotation.details.position;
    let timeKeys = Object.keys(position).sort(
      (a, b) => parseFloat(a) - parseFloat(b),
    );
    for (let i = 0; i < timeKeys.length; i++) {
      let t1 = null,
        t2 = null;
      if (timeKeys[i + 1]) {
        t1 = timeKeys[i];
        t2 = timeKeys[i + 1];
        annotation.details["intPositions"][t1] = position[t1];
      } else {
        annotation.details["intPositions"][timeKeys[i]] = position[timeKeys[i]];
        break;
      }
      let maxTimeInterval = 1 / 30;
      let tdiff = parseFloat(t2) - parseFloat(t1);
      let incr = Math.floor(tdiff / maxTimeInterval);
      if (tdiff <= maxTimeInterval) continue;
      let xincr = (position[t2][0] - position[t1][0]) / incr;
      let yincr = (position[t2][1] - position[t1][1]) / incr;
      let wincr = null,
        hincr = null;
      if (
        position[t1][2] &&
        position[t1][3] &&
        position[t2][2] &&
        position[t2][3]
      ) {
        wincr = (position[t2][2] - position[t1][2]) / incr;
        hincr = (position[t2][3] - position[t1][3]) / incr;
      }
      for (let j = 1; j < incr; j++) {
        let tmid = parseFloat(t1) + j * maxTimeInterval;
        let xmid = position[t1][0] + j * xincr;
        let ymid = position[t1][1] + j * yincr;
        let wmid = null,
          hmid = null;
        if (wincr && hincr) {
          wmid = position[t1][2] + j * wincr;
          if (xmid + wmid > 100) wmid = 100 - xmid;
          hmid = position[t1][3] + j * hincr;
          if (ymid + hmid > 100) hmid = 100 - ymid;
          annotation.details["intPositions"][tmid] = [xmid, ymid, wmid, hmid];
        } else {
          annotation.details["intPositions"][tmid] = [xmid, ymid];
        }
      }
    }
  }

  onProgress() {
    this.state.currentTime = this.videoElem.currentTime;
    this.timeCache = this.state.currentTime;

    if (this.videoElem.duration > 0) {
      const played = this.state.currentTime / this.videoElem.duration;
      this.updateTimeDisplay();
      this.updateScrubber(played);
      this.updateBufferedBar();
    }

    this.applyAnnotations();
  }

  updateBufferedBar() {
    if (!this.controls.scrubberBuffered || !this.videoElem.duration) return;
    const buffered = this.videoElem.buffered;
    let maxBuffered = 0;
    for (let i = 0; i < buffered.length; i++) {
      if (buffered.end(i) > maxBuffered) {
        maxBuffered = buffered.end(i);
      }
    }
    const percent = Math.max(0, Math.min(1, maxBuffered / this.videoElem.duration));
    this.controls.scrubberBuffered.style.width = `${percent * 100}%`;
  }

  updateTimeDisplay() {
    if (!this.controls.playTime) return;

    const formatTime = (timeInSeconds) => {
        const time = Math.round(timeInSeconds);
        const hours = Math.floor(time / 3600);
        const minutes = Math.floor((time % 3600) / 60);
        const seconds = time % 60;

        if (hours > 0) {
            return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }
        return `${minutes}:${seconds.toString().padStart(2, '0')}`;
    };

    const currentTimeStr = formatTime(this.state.currentTime);
    const durationStr = formatTime(this.videoElem.duration || 0);

    this.controls.playTime.textContent = `${currentTimeStr} / ${durationStr}`;
  }

  updateScrubber(played) {
    if (this.controls.scrubberProgress) {
      this.controls.scrubberProgress.style.width = `${played * 100}%`;
    }
    if (this.controls.scrubberDot) {
      this.controls.scrubberDot.style.left = `calc(${played * 100}% - 7px)`;
    }
  }

  onScrubberClick(e) {
    if (!this.controls.scrubber) return;
    if (this.isDragging) return;

    const rect = this.controls.scrubber.getBoundingClientRect();
    const percent = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const newTime = percent * (this.videoElem.duration || 0);
    this.skipTo(newTime);
  }

  onScrubberDrag(e) {
    if (!this.controls.scrubber) return;
    const rect = this.controls.scrubber.getBoundingClientRect();
    const percent = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    let newTime = percent * (this.videoElem.duration || 0);

    const skipBoundary = this._getSkipBoundary(newTime);
    if (skipBoundary !== null) {
      newTime = skipBoundary;
    }

    const adjustedPercent = newTime / (this.videoElem.duration || 1);
    this.updateScrubber(adjustedPercent);
    this.state.currentTime = newTime;
    this.updateTimeDisplay();

    this.videoElem.currentTime = newTime;
    this.timeCache = newTime;
  }

  _getSkipBoundary(time, direction = 'nearest') {
    if (!this.annotations) return null;

    const skipEvents = this.annotations.filter(event =>
      (event.type === 'Skip' || event.type === 'skip')
    );

    for (const skip of skipEvents) {
      const start = parseFloat(skip.start);
      const end = parseFloat(skip.end);

      if (time > start && time < end) {
        if (direction === 'forward') {
          return end;
        } else if (direction === 'backward') {
          return start;
        } else {
          // For dragging (nearest), snap to closest boundary
          const distToStart = time - start;
          const distToEnd = end - time;
          return distToStart < distToEnd ? start : end;
        }
      }
    }

    return null;
  }

  beginScrubberDrag(e) {
    if (!this.controls.scrubber) return;
    this.isDragging = true;
    this.wasPlayingBeforeDrag = !this.videoElem.paused;
    if (this.wasPlayingBeforeDrag) {
      this.videoElem.pause();
    }
    this.controls.scrubber.classList.add('scrubber-dragging');
    this.onScrubberDrag(e);

    // Prevent text selection while dragging
    e.preventDefault();

    // Start following mouse movement with RAF for smoothness
    const moveHandler = (moveEvent) => {
      this.onScrubberDrag(moveEvent);
    };
    const upHandler = () => {
      this.endScrubberDrag();
      document.removeEventListener('mousemove', moveHandler);
      document.removeEventListener('mouseup', upHandler);
    };
    document.addEventListener('mousemove', moveHandler);
    document.addEventListener('mouseup', upHandler);
  }

  endScrubberDrag() {
    if (this.isDragging && this.wasPlayingBeforeDrag) {
      this.videoElem.play();
    }
    if (this.controls.scrubber) {
      this.controls.scrubber.classList.remove('scrubber-dragging');
    }
    this.isDragging = false;
    this.wasPlayingBeforeDrag = false;
  }

  toggleFullscreen() {
    if (!this.controls.container) return;

    const elem = this.controls.container;

    if (!this.state.fullscreen) {
      if (elem.requestFullscreen) elem.requestFullscreen();
      else if (elem.mozRequestFullScreen) elem.mozRequestFullScreen();
      else if (elem.webkitRequestFullscreen) elem.webkitRequestFullscreen();
      else if (elem.msRequestFullscreen) elem.msRequestFullscreen();
    } else {
      if (document.exitFullscreen) document.exitFullscreen();
      else if (document.mozCancelFullScreen) document.mozCancelFullScreen();
      else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      else if (document.msExitFullscreen) document.msExitFullscreen();
    }

    this.state.fullscreen = !this.state.fullscreen;
    this._updateFullscreenIcon();
  }

  onFullscreenChange() {
    const isFullscreen = !!(document.fullscreenElement || document.webkitIsFullScreen ||
      document.mozFullScreen || document.msFullscreenElement);

    if (this.state.fullscreen !== isFullscreen) {
      this.state.fullscreen = isFullscreen;
      this._updateFullscreenIcon();
    }
  }

  _updateFullscreenIcon() {
    if (!this.controls.fullscreenBtn) return;

    if (this.state.fullscreen) {
      this.controls.fullscreenBtn.innerHTML = AnnotationPlayer.icons.fullscreenBtn.exit;
    } else {
      this.controls.fullscreenBtn.innerHTML = AnnotationPlayer.icons.fullscreenBtn.enter;
    }
  }

  _renderSpeedMenu() {
    if (!this.controls.speedMenu) return;
    this.controls.speedMenu.innerHTML = this.playbackRates.map(rate =>
      `<div class="speed-option${rate === this.state.playbackRate ? ' active-value' : ''}" data-speed="${rate}" style="padding:4px 16px;cursor:pointer;">${rate}x</div>`
    ).join('');
  }

  setPlaybackRate(rate) {
    this.state.playbackRate = rate;
    this.videoElem.playbackRate = rate;

    if (this.controls.speedBtn) {
      const speedText = this.controls.speedBtn.querySelector('.speed-text');
      if (speedText) {
        speedText.textContent = `${rate}x`;
      }
    }
    this._renderSpeedMenu();
  }

    _loadSubtitleTracks(subtitleObjs) {
    subtitleObjs.forEach((trackData, index) => {
      const subtitleTrackElem = document.createElement('track');

      // default
      subtitleTrackElem.kind = trackData.kind || 'subtitles';

      if (trackData.label) subtitleTrackElem.label = trackData.label;
      if (trackData.srclang) subtitleTrackElem.srclang = trackData.srclang;
      subtitleTrackElem.default = false;  // all subtitleTracks start disabled

      // Set src - either from url or vtt content
      if (trackData.url) {
        subtitleTrackElem.src = trackData.url;
      } else if (trackData.vtt) {
        if (!trackData.vtt.trim().startsWith('WEBVTT')) {
          console.error(`Subtitle track ${index}: VTT content must start with 'WEBVTT'. Provided content:`, trackData.vtt.substring(0, 50));
          return;
        }

        // Create blob URL for inline VTT content
        const blob = new Blob([trackData.vtt], { type: 'text/vtt' });
        const blobUrl = URL.createObjectURL(blob);
        subtitleTrackElem.src = blobUrl;
        this.subtitleTrackBlobUrls.push(blobUrl);  // Keep subtitle track for cleanup
      } else {
        console.error(`Subtitle track ${index}: Subtitle object must have either 'url' or 'vtt' property`);
        return;
      }

      this.videoElem.appendChild(subtitleTrackElem);
    });

    const onTracksReady = () => {
      if (this.controls.captionsMenu) {
        this._renderCaptionsMenu();
      }

      if (this._enableSubtitleSidebar && !this.subtitleSidebar) {
        this.subtitleSidebar = new SubtitleSidebar(this.videoElem);
      }

      this._conditionallyUpdateControlsIconVisibility();
    };

    if (this.videoElem.readyState >= 1) {
      setTimeout(onTracksReady, 50);
    } else {
      this.videoElem.addEventListener('loadedmetadata', () => {
        setTimeout(onTracksReady, 50);
      }, { once: true });
    }
  }

  _renderCaptionsMenu() {
    if (!this.controls.captionsMenu) return;

    const subtitleTrackElems = Array.from(this.videoElem.textTracks);

    let menuHTML = '<div class="caption-option" data-subtitle-track="off" style="padding:8px 16px;cursor:pointer;white-space:nowrap;">Off</div>';

    subtitleTrackElems.forEach((trackElem, index) => {
      let label = trackElem.label || `Subtitle track ${index + 1}`;
      if (trackElem.language) {
        label += ` (${trackElem.language})`;
      }
      menuHTML += `<div class="caption-option" data-subtitle-track="${index}" style="padding:8px 16px;cursor:pointer;white-space:nowrap;">${label}</div>`;
    });

    this.controls.captionsMenu.innerHTML = menuHTML;
    this.controls.captionsMenu.style.minWidth = '180px';

    this._updateCaptionsMenuHighlight();
  }

  _updateCaptionsMenuHighlight() {
    if (!this.controls.captionsMenu) return;

    const activeIndex = this._getActiveTrackIndex();

    this.controls.captionsMenu.querySelectorAll('.caption-option').forEach(option => {
      option.classList.remove('active-value');
    });

    if (activeIndex !== -1) {
      const activeOption = this.controls.captionsMenu.querySelector(`[data-subtitle-track="${activeIndex}"]`);
      if (activeOption) {
        activeOption.classList.add('active-value');
      }
    } else {
      const offOption = this.controls.captionsMenu.querySelector('[data-subtitle-track="off"]');
      if (offOption) {
        offOption.classList.add('active-value');
      }
    }
  }

  setCaptionTrack(trackIndex) {
    const subtitleTrackElems = Array.from(this.videoElem.textTracks);
    subtitleTrackElems.forEach(trackElem => {
      trackElem.mode = 'disabled';
    });

    if (trackIndex !== 'off' && subtitleTrackElems[trackIndex]) {
      subtitleTrackElems[trackIndex].mode = 'showing';
    }

    this._updateCaptionsMenuHighlight();
    this._updateSubtitleSidebarButtonDisplay();

    if (this.subtitleSidebar) {
      this.subtitleSidebar.onTrackChanged();
    }
  }

  destroy() {
    this.subtitleTrackBlobUrls.forEach(url => {
      URL.revokeObjectURL(url);
    });
    this.subtitleTrackBlobUrls = [];

    if (this.subtitleSidebar) {
      this.subtitleSidebar.destroy();
      this.subtitleSidebar = null;
    }

    this.resetAnnotations();
  }

  onMouseMove() {
    this.state.mouseInactive = false;

    if (this.controls.container) {
      this.controls.container.classList.remove('cursor-hidden');
    }

    this.refreshControlsVisibility();

    if (this.mouseTimer) clearTimeout(this.mouseTimer);

    this.mouseTimer = setTimeout(() => {
      this.state.mouseInactive = true;
      if (this.controls.container) {
        this.controls.container.classList.add('cursor-hidden');
      }
      this.refreshControlsVisibility();
    }, 3000);
  }

  refreshControlsVisibility() {
    const shouldShow = (!this.state.mouseInactive && this.state.hovering) ||
                       !this.state.playing ||
                       this.state.controlsHovering;
    if (this.container) {
      if (shouldShow) {
        this.container.classList.remove('controls-hidden');
      } else {
        this.container.classList.add('controls-hidden');
      }
    }
  }

  onKeydown(e) {
    this.onMouseMove();  // to trigger controls visibility/fade
    const playedTime = this.state.currentTime;

    if (this.isMuteAnnotationActive && (
      e.code === 'ArrowUp' ||
      e.code === 'ArrowDown' ||
      e.code === 'KeyM'
    )) {
      e.preventDefault();
      return;
    }

    switch (e.code) {
      case 'Space':
        e.preventDefault();
        this.togglePlayPause();
        if (this.videoElem.paused) {
          this._showBezel(AnnotationPlayer.icons.playPauseBtn.pause);
        } else {
          this._showBezel(AnnotationPlayer.icons.playPauseBtn.play);
        }
        break;
      case 'ArrowRight': {
        e.preventDefault();
        let newTimeRight = this.videoElem.paused ? playedTime + 0.1 : playedTime + 5;
        const skipBoundaryRight = this._getSkipBoundary(newTimeRight, 'forward');
        if (skipBoundaryRight !== null) {
          newTimeRight = skipBoundaryRight;
        }
        this.skipTo(newTimeRight);
        this._showBezel(AnnotationPlayer.icons.speed);
        break;
      }
      case 'ArrowLeft': {
        e.preventDefault();
        let skipAmount = this.videoElem.paused ? 0.1 : 5;
        let newTimeLeft = playedTime - skipAmount;
        const skipBoundaryLeft = this._getSkipBoundary(newTimeLeft, 'backward');
        if (skipBoundaryLeft !== null) {
          // If paused, go to start of skip; if playing, go 5 seconds before skip
          newTimeLeft = this.videoElem.paused ? skipBoundaryLeft : Math.max(0, skipBoundaryLeft - 5);
        }
        this.skipTo(newTimeLeft);
        this._showBezel(AnnotationPlayer.icons.speedLeft);
        break;
      }
      case 'ArrowUp':
        e.preventDefault();
        this.setVolume(this.state.volume + 0.1);
        this._showBezel(this._getVolumeIcon(), `${Math.round(this.state.volume * 100)}%`);
        break;
      case 'ArrowDown':
        e.preventDefault();
        this.setVolume(this.state.volume - 0.1);
        this._showBezel(this._getVolumeIcon(), `${Math.round(this.state.volume * 100)}%`);
        break;
      case 'KeyM':
        e.preventDefault();
        this.toggleMute();
        this._showBezel(this.videoElem.muted ? AnnotationPlayer.icons.volume.mute : this._getVolumeIcon());
        break;
      case 'Period':
        e.preventDefault();
        if (e.shiftKey) { // '>'
          const currentIndex = this.playbackRates.indexOf(this.state.playbackRate);
          if (currentIndex < this.playbackRates.length - 1) {
            this.setPlaybackRate(this.playbackRates[currentIndex + 1]);
            this._showBezel(AnnotationPlayer.icons.speed, `${this.playbackRates[currentIndex + 1]}x`);
          }
        } else if (this.paused) { // '.'
          let newTimeRight = playedTime + 0.1 * this.state.playbackRate;
          const skipBoundaryRight = this._getSkipBoundary(newTimeRight, 'forward');
          if (skipBoundaryRight !== null) {
            newTimeRight = skipBoundaryRight;
          }
          this.skipTo(newTimeRight);
          console.log("Time after microskip: ", this.videoElem.currentTime);
        }
        break;
      case 'Comma':
        e.preventDefault();
        if (e.shiftKey) {  // '<'
          const currentIndex = this.playbackRates.indexOf(this.state.playbackRate);
          if (currentIndex > 0) {
            this.setPlaybackRate(this.playbackRates[currentIndex - 1]);
            this._showBezel(AnnotationPlayer.icons.speed, `${this.playbackRates[currentIndex - 1]}x`);
          }
        } else if (this.paused) { // ','
          let newTimeLeft = playedTime - 0.1 * this.state.playbackRate;
          const skipBoundaryLeft = this._getSkipBoundary(newTimeLeft, 'backward');
          if (skipBoundaryLeft !== null) {
            newTimeLeft = skipBoundaryLeft;
          }
          this.skipTo(newTimeLeft);
          console.log("Time after microskip: ", this.videoElem.currentTime);
        }
        break;
      case 'KeyF':
        e.preventDefault();
        this.toggleFullscreen();
        break;
    }
  }

  setupEventListeners() {
    this.videoElem.addEventListener('loadedmetadata', () => {
      this.renderSkipsOnScrubber();
      this._conditionallyUpdateControlsIconVisibility();
    });

    this.videoElem.addEventListener('timeupdate', () => this.onProgress());

    this.videoElem.addEventListener('play', () => {
      this.paused = false;
      this.state.playing = true;
      this.state.started = true;

      if (this.controls.playPauseBtn) {
        this.controls.playPauseBtn.classList.add('playing');
        this._updatePlayPauseIcon();
      }
    });

    this.videoElem.addEventListener('pause', () => {
      this.paused = true;
      this.state.playing = false;

      if (this.controls.playPauseBtn) {
        this.controls.playPauseBtn.classList.remove('playing');
        this._updatePlayPauseIcon();
      }
    });

    this.videoElem.addEventListener('click', (e) => {
      e.preventDefault();
      this.togglePlayPause();
      this.onMouseMove();  // to trigger controls visibility/fade
    });

    if (this.controls.playPauseBtn) {
      this.controls.playPauseBtn.addEventListener('click', (e) => {
        e.preventDefault();
        this.togglePlayPause();
      });
    }

    if (this.controls.volumeBtn) {
        this.controls.volumeBtn.addEventListener('click', () => this.toggleMute());
    }

    if (this.controls.volumeSlider) {
        this.controls.volumeSlider.addEventListener('input', (e) => {
            if (!this.isMuteAnnotationActive) {
              // Quantize slider value to nearest 0.1
              const quantized = Math.round(parseFloat(e.target.value) * 10) / 10;
              this.setVolume(quantized);
            }
        });
    }

    if (this.controls.scrubber) {
      this.controls.scrubber.addEventListener('click', (e) => this.onScrubberClick(e));

      this.controls.scrubber.addEventListener('mousedown', (e) => {
        this.beginScrubberDrag(e);
      });
    }

    if (this.controls.fullscreenBtn) {
      this.controls.fullscreenBtn.addEventListener('click', () => this.toggleFullscreen());
    }

    document.addEventListener('fullscreenchange', () => this.onFullscreenChange());
    document.addEventListener('webkitfullscreenchange', () => this.onFullscreenChange());
    document.addEventListener('mozfullscreenchange', () => this.onFullscreenChange());
    document.addEventListener('MSFullscreenChange', () => this.onFullscreenChange());

    if (this.controls.container) {
      this.controls.container.addEventListener('mousemove', () => this.onMouseMove());

      this.controls.container.addEventListener('mouseenter', () => {
        this.state.hovering = true;
        this.state.mouseInactive = false; // Reset inactive state on enter
        if (this.mouseTimer) clearTimeout(this.mouseTimer); // Clear any pending timer
        if (this.controls.container) {
          this.controls.container.classList.remove('cursor-hidden');
        }
        this.refreshControlsVisibility();

        // Restart the inactivity timer
        this.mouseTimer = setTimeout(() => {
          this.state.mouseInactive = true;
          if (this.controls.container) {
            this.controls.container.classList.add('cursor-hidden');
          }
          this.refreshControlsVisibility();
        }, 3000);
      });

      this.controls.container.addEventListener('mouseleave', () => {
        this.state.hovering = false;
        if (this.mouseTimer) clearTimeout(this.mouseTimer); // Clear timer when leaving
        this.refreshControlsVisibility();
      });

      const controlButtons = this.controls.container.querySelectorAll('#returnBtn, #reloadAnnotationsBtn, .video-controls');
      controlButtons.forEach(button => {
        button.addEventListener('mouseenter', () => {
          this.state.controlsHovering = true;
          this.refreshControlsVisibility();
        });
        button.addEventListener('mouseleave', () => {
          this.state.controlsHovering = false;
          this.refreshControlsVisibility();
        });
      });
    }

    this.videoElem.addEventListener('mousemove', () => this.onMouseMove());
    this.annotationBox.addEventListener('mousemove', () => this.onMouseMove());

    // Add document-level mousemove listener to catch mouse movement that might be missed
    // This ensures controls can always be triggered even if state gets out of sync
    document.addEventListener('mousemove', (e) => {
      if (this.container && this.container.contains(e.target)) {
        if (!this.state.hovering) {
          this.state.hovering = true;
        }
        this.onMouseMove();
      }
    });

    document.addEventListener('keydown', (e) => this.onKeydown(e));

    const resizeObserver = new ResizeObserver((entries) => {
      if (!this.aspectRatio) {
        this.setAspectRatio();
      }
      for (let entry of entries) {
        if (entry.target == this.container) {
          this.setVideoWrapperStyling();
        }
      }
    });
    resizeObserver.observe(this.container);

    if (this.controls.speedBtn && this.controls.speedMenu) {
      this._renderSpeedMenu();
      this.controls.speedBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const menu = this.controls.speedMenu;
        menu.style.display = (menu.style.display === 'none' || !menu.style.display) ? 'block' : 'none';
        if (this.controls.captionsMenu) {
          this.controls.captionsMenu.style.display = 'none';
        }
      });
      this.controls.speedMenu.addEventListener('click', (e) => {
        const target = e.target.closest('.speed-option');
        if (target) {
          const rate = parseFloat(target.dataset.speed);
          this.setPlaybackRate(rate);
          this.controls.speedMenu.style.display = 'none';
        }
      });
    }

    if (this.controls.clipsBtn && this.controls.clipsMenu) {
      this.controls.clipsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const menu = this.controls.clipsMenu;
        menu.style.display = (menu.style.display === 'none' || !menu.style.display) ? 'block' : 'none';
        if (this.controls.speedMenu) {
          this.controls.speedMenu.style.display = 'none';
        }
        if (this.controls.captionsMenu) {
          this.controls.captionsMenu.style.display = 'none';
        }
      });
      this.controls.clipsMenu.addEventListener('click', (e) => {
        const target = e.target.closest('.clip-option');
        if (target) {
          const clipIndex = target.dataset.clip;
          this.setActiveClip(clipIndex === 'off' ? 'off' : parseInt(clipIndex));
          this.controls.clipsMenu.style.display = 'none';
        }
      });
    }

    if (this.controls.captionsBtn && this.controls.captionsMenu) {
      this.controls.captionsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const menu = this.controls.captionsMenu;
        menu.style.display = (menu.style.display === 'none' || !menu.style.display) ? 'block' : 'none';
        if (this.controls.speedMenu) {
          this.controls.speedMenu.style.display = 'none';
        }
      });
      this.controls.captionsMenu.addEventListener('click', (e) => {
        const target = e.target.closest('.caption-option');
        if (target) {
          const trackIndex = target.dataset.subtitleTrack;
          this.setCaptionTrack(trackIndex === 'off' ? 'off' : parseInt(trackIndex));
          this.controls.captionsMenu.style.display = 'none';
        }
      });
    }

    if (this.controls.subtitleSidebarBtn) {
      this.controls.subtitleSidebarBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();

        if (this.controls.subtitleSidebarBtn.classList.contains('inactive') || this.controls.subtitleSidebarBtn.disabled) {
          return;
        }

        if (this.subtitleSidebar) {
          this.subtitleSidebar.toggle();
        }
      });
    }

    // Hide menus on outside click
    document.addEventListener('click', () => {
      if (this.controls.speedMenu) {
        this.controls.speedMenu.style.display = 'none';
      }
      if (this.controls.clipsMenu) {
        this.controls.clipsMenu.style.display = 'none';
      }
      if (this.controls.captionsMenu) {
        this.controls.captionsMenu.style.display = 'none';
      }
    });
  }
}
