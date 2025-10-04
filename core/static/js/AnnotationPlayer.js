export class AnnotationPlayer {
  constructor(options = {}) {
    // New approach: require a container element
    this.container = this._getElement(options.container);
    if (!this.container) {
      throw new Error('AnnotationPlayer requires a container element');
    }

    // Get or create video element
    this.videoElem = this._getElement(options.video) || this.container.querySelector('video');
    if (!this.videoElem) {
      this.videoElem = document.createElement('video');
      this.container.appendChild(this.videoElem);
    }

    // Get or create annotation container
    this.annotationContainer = this._getElement(options.annotationContainer) ||
                               this.container.querySelector('.annotation-container');
    if (!this.annotationContainer) {
      this.annotationContainer = document.createElement('div');
      this.annotationContainer.className = 'annotation-container';
      this.container.appendChild(this.annotationContainer);
    }

    // Create controls dynamically
    this.disabledControls = options.disabledControls || [];
    this._createControls();

    // Disable native video controls
    this.videoElem.controls = false;

    this.state = {
      playing: false,
      started: false,
      duration: 0,
      currentTime: 0,
      playbackRate: 1.0,
      muted: false,
      fullscreen: false,
      showTranscript: false,
      mouseInactive: false,
      hovering: false,
      controlsHovering: false,
      displaySubtitles: null,
      subtitleTextIndex: null,
    };

    this.annotations = [];
    this.subtitles = [];
    this.currently = { muting: -1, blanking: -1, blurring: -1 };

    this.mouseTimer = null;
    this.controlsTimeout = null;
    this.timeCache = 0;

    this.initEventListeners();
    this.placeAnnotationContainer();
  }

  static icons = {
    playPauseBtn: {
      play: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`,
      pause: `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>`
    },
    speedBtn: `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" fill="none"/><text x="12" y="16" text-anchor="middle" font-size="10" fill="currentColor">1x</text></svg>`,
    captionsBtn: `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="2" y="6" width="20" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M6 10h4M6 14h4M14 10h4M14 14h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
    transcriptBtn: `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 8h8M8 12h8M8 16h5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
    fullscreenBtn: {
      enter: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>`,
      exit: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>`
    }
  };

  _getElement(selector) {
    if (!selector) return null;
    if (selector instanceof HTMLElement) return selector;
    if (typeof selector === 'string') return document.querySelector(selector);
    return null;
  }

  _createControls() {
    // Initialize controls object
    this.controls = {};

    // Create control bar HTML
    const controlBarHTML = `
      <div class="video-controls">
        <button class="play-pause-btn" aria-label="Play/Pause"></button>
        <div class="scrubber">
          <div class="scrubber-progress"></div>
          <div class="scrubber-dot"></div>
        </div>
        <div class="play-time">00:00:00</div>
        <button class="speed-btn" aria-label="Playback Speed"><span class="speed-text">1x</span></button>
        <button class="captions-btn" aria-label="Captions"></button>
        <button class="transcript-btn" aria-label="Transcript"></button>
        <button class="fullscreen-btn" aria-label="Fullscreen"></button>
      </div>
      <div class="subtitle-text"></div>
    `;

    // Create a temporary container to parse the HTML
    const temp = document.createElement('div');
    temp.innerHTML = controlBarHTML;

    // Append controls to container
    while (temp.firstChild) {
      this.container.appendChild(temp.firstChild);
    }

    // Store references to controls (skip disabled ones)
    if (!this.disabledControls.includes('playPauseBtn')) {
      this.controls.playPauseBtn = this.container.querySelector('.play-pause-btn');
      this.controls.playPauseBtn.innerHTML = AnnotationPlayer.icons.playPauseBtn.play;
    }
    if (!this.disabledControls.includes('scrubber')) {
      this.controls.scrubber = this.container.querySelector('.scrubber');
      this.controls.scrubberProgress = this.container.querySelector('.scrubber-progress');
      this.controls.scrubberDot = this.container.querySelector('.scrubber-dot');
    }
    if (!this.disabledControls.includes('playTime')) {
      this.controls.playTime = this.container.querySelector('.play-time');
    }
    if (!this.disabledControls.includes('speedBtn')) {
      this.controls.speedBtn = this.container.querySelector('.speed-btn');
    }
    if (!this.disabledControls.includes('captionsBtn')) {
      this.controls.captionsBtn = this.container.querySelector('.captions-btn');
      this.controls.captionsBtn.innerHTML = AnnotationPlayer.icons.captionsBtn;
    }
    if (!this.disabledControls.includes('transcriptBtn')) {
      this.controls.transcriptBtn = this.container.querySelector('.transcript-btn');
      this.controls.transcriptBtn.innerHTML = AnnotationPlayer.icons.transcriptBtn;
    }
    if (!this.disabledControls.includes('fullscreenBtn')) {
      this.controls.fullscreenBtn = this.container.querySelector('.fullscreen-btn');
      this.controls.fullscreenBtn.innerHTML = AnnotationPlayer.icons.fullscreenBtn.enter;
    }
    if (!this.disabledControls.includes('subtitleText')) {
      this.controls.subtitleText = this.container.querySelector('.subtitle-text');
    }

    // Remove disabled controls from DOM
    this.disabledControls.forEach(controlName => {
      const classMap = {
        'playPauseBtn': '.play-pause-btn',
        'scrubber': '.scrubber',
        'playTime': '.play-time',
        'speedBtn': '.speed-btn',
        'captionsBtn': '.captions-btn',
        'transcriptBtn': '.transcript-btn',
        'fullscreenBtn': '.fullscreen-btn',
        'subtitleText': '.subtitle-text'
      };
      const selector = classMap[controlName];
      if (selector) {
        const element = this.container.querySelector(selector);
        if (element) element.remove();
      }
    });

    // Set container reference for fullscreen and other operations
    this.controls.container = this.container;
  }

  placeAnnotationContainer() {
    const videoRect = this.videoElem.getBoundingClientRect();

    const videoWidth = this.videoElem.videoWidth;
    const videoHeight = this.videoElem.videoHeight;
    if (!videoWidth || !videoHeight) {
      return;
    }

    const elemWidth = this.videoElem.clientWidth;
    const elemHeight = this.videoElem.clientHeight;

    const videoAspect = videoWidth / videoHeight;
    const elemAspect = elemWidth / elemHeight;

    let displayWidth, displayHeight, offsetLeft, offsetTop;

    if (elemAspect > videoAspect) {
      // Black bars on left/right
      displayHeight = elemHeight;
      displayWidth = elemHeight * videoAspect;
      offsetLeft = (elemWidth - displayWidth) / 2;
      offsetTop = 0;
    } else {
      // Black bars on top/bottom
      displayWidth = elemWidth;
      displayHeight = elemWidth / videoAspect;
      offsetLeft = 0;
      offsetTop = (elemHeight - displayHeight) / 2;
    }

    const annotationContainer = this.annotationContainer;
    annotationContainer.style.position = "absolute";
    annotationContainer.style.pointerEvents = "none";
    annotationContainer.style.left = `${videoRect.left + window.scrollX + offsetLeft}px`;
    annotationContainer.style.top = `${videoRect.top + window.scrollY + offsetTop}px`;
    annotationContainer.style.width = `${displayWidth}px`;
    annotationContainer.style.height = `${displayHeight}px`;
    annotationContainer.style.zIndex = 10;
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

  /**
   * Load annotation data including events and subtitles.
   * Supports both array and object input.
   */
  loadData(data) {
    if (!data.annotations && Array.isArray(data)) {
      this.annotations = data;
      this.subtitles = [];
      this.clips = [];
    } else {
      this.annotations = data.annotations || [];
      this.subtitles = data.subtitles || [];
      this.clips = data.clips || [];
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
    this.annotate();
    this.renderSkipsOnScrubber(); // Add this to render skip markers after loading data

    // Also update skip markers when video metadata is loaded (duration available)
    this.videoElem.addEventListener('loadedmetadata', () => {
      this.renderSkipsOnScrubber();
    });
  }

  /**
   * Render skip event markers on the scrubber.
   */
  renderSkipsOnScrubber() {
    if (!this.controls.scrubber || !this.annotations || !this.state.duration) return;

    // Remove existing skip markers
    this.controls.scrubber.querySelectorAll('.skip-on-scrubber').forEach(el => el.remove());

    const skipEvents = this.annotations.filter(event =>
      event.type === 'Skip' || event.type === 'skip'
    );

    skipEvents.forEach(event => {
      const startPercent = (parseFloat(event.start) / this.state.duration) * 100;
      const endPercent = (parseFloat(event.end) / this.state.duration) * 100;

      const skipElement = document.createElement('div');
      skipElement.className = 'skip-on-scrubber';
      skipElement.style.left = `${startPercent}%`;
      skipElement.style.width = `${endPercent - startPercent}%`;

      this.controls.scrubber.appendChild(skipElement);
    });
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

  annotate() {
    this._onPlaying = () => this.applyAnnotations();
    this.currently = { muting: -1, blanking: -1, blurring: -1 };
    this.videoElem.addEventListener("playing", this._onPlaying);
  }

  applyAnnotations() {
    if (!this.annotations) return;
    let time = this.videoElem.currentTime;
    this.timeCache = time;
    this.state.currentTime = time;

    let numAnnotations = this.annotations.length;
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
              if (!vMuted) {
                this.currently.muting = i;
                this.mute();
              }
            } else {
              if (vMuted) {
                this.currently.muting = -1;
                this.unmute();
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
            if (!this.annotationContainer.querySelector("#censor" + i)) {
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
              this.annotationContainer.appendChild(censor);
            } else {
              const censor = this.annotationContainer.querySelector(
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
            const existingCensor = this.annotationContainer.querySelector(
              "#censor" + i,
            );
            if (existingCensor) {
              existingCensor.remove();
            }
          }
          break;
      }
    }
    if (this.videoElem.paused) return;
    requestAnimationFrame(() => this.applyAnnotations());
  }

  resetAnnotations() {
    this.videoElem.removeEventListener("playing", this._onPlaying);
    this.videoElem.classList.remove("blanked");
    this.videoElem.classList.remove("blurred");
    Array.from(
      this.annotationContainer.querySelectorAll("[id^=censor]"),
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
    // TODO Make this subtype of blanked with CSS options
  }

  unblur() {
    this.videoElem.classList.remove("blurred");
  }

  mute() {
    this.videoElem.muted = true;
  }

  unmute() {
    this.videoElem.muted = false;
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

  handleProgress() {
    this.state.currentTime = this.videoElem.currentTime;
    this.timeCache = this.state.currentTime;

    if (this.state.duration > 0) {
      const played = this.state.currentTime / this.state.duration;
      this.updateTimeDisplay();
      this.updateScrubber(played);
    }

    this.handleSubtitles();
    this.applyAnnotations();
  }

  updateTimeDisplay() {
    if (!this.controls.playTime) return;

    const time = this.state.currentTime;
    const hours = Math.floor(time / 3600);
    const minutes = Math.floor((time % 3600) / 60);
    const seconds = Math.floor(time % 60);
    const timeString = `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;

    this.controls.playTime.textContent = timeString;
  }

  updateScrubber(played) {
    if (this.controls.scrubberProgress) {
      this.controls.scrubberProgress.style.width = `${played * 100}%`;
    }
    if (this.controls.scrubberDot) {
      this.controls.scrubberDot.style.left = `calc(${played * 100}% - 3px)`;
    }
  }

  handleSeekClick(e) {
    if (!this.controls.scrubber) return;

    const rect = this.controls.scrubber.getBoundingClientRect();
    const percent = (e.clientX - rect.left) / rect.width;
    const newTime = percent * this.state.duration;
    this.skipTo(newTime);
  }

  handleToggleFullscreen() {
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

  handleFullscreenChange() {
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

  handlePlaybackRateChange(rate) {
    this.state.playbackRate = rate;
    this.videoElem.playbackRate = rate;

    if (this.controls.speedBtn) {
      const speedText = this.controls.speedBtn.querySelector('.speed-text');
      if (speedText) {
        speedText.textContent = `${rate}x`;
      }
    }

    document.querySelectorAll('.speed-option').forEach(btn => {
      btn.classList.toggle('active-value', parseFloat(btn.dataset.speed) === rate);
    });
  }

  handleCaptionChange(lang) {
    document.querySelectorAll('.caption-option').forEach(btn => {
      btn.classList.toggle('active-value', btn.dataset.lang === lang);
    });

    if (lang === 'off') {
      this.state.displaySubtitles = null;
    } else {
      this.state.displaySubtitles = this.subtitles.find(sub => sub.language === lang);
    }
  }

  handleSubtitles() {
    if (!this.controls.subtitleText || !this.state.displaySubtitles?.content) {
      if (this.controls.subtitleText) {
        this.controls.subtitleText.textContent = '';
      }
      return;
    }

    const subtitle = this.state.displaySubtitles.content.find(
      sub => this.state.currentTime >= sub.start &&
             this.state.currentTime <= (sub.end || sub.start + 5)
    );

    this.controls.subtitleText.textContent = subtitle ? subtitle.text : '';
  }

  handleMouseMoved() {
    this.state.mouseInactive = false;

    if (this.controls.container) {
      this.controls.container.classList.remove('cursor-hidden');
    } else {
      this.videoElem.controls = true;
    }

    this.updateControlsVisibility();

    if (this.mouseTimer) clearTimeout(this.mouseTimer);

    this.mouseTimer = setTimeout(() => {
      this.state.mouseInactive = true;
      if (this.controls.container) {
        this.controls.container.classList.add('cursor-hidden');
      }
      this.updateControlsVisibility();
    }, 3000);
  }

  updateControlsVisibility() {
    const shouldShow = (!this.state.mouseInactive && this.state.hovering) ||
                       !this.state.playing ||
                       this.state.controlsHovering;
    if (this.container) {
      this.container.classList.toggle('controls-hidden', !shouldShow);
    }
  }

  handleKeydown(e) {
    const playedTime = this.state.currentTime;

    switch (e.code) {
      case 'Space':
        e.preventDefault();
        this.togglePlayPause();
        break;
      case 'ArrowRight':
        e.preventDefault();
        this.skipTo(this.videoElem.paused ? playedTime + 0.1 : playedTime + 5);
        break;
      case 'ArrowLeft':
        e.preventDefault();
        this.skipTo(this.videoElem.paused ? playedTime - 0.1 : playedTime - 5);
        break;
      case 'Period':
        e.preventDefault();
        if (e.shiftKey) {
          const rates = [0.5, 0.75, 1, 1.25, 1.5, 2];
          const currentIndex = rates.indexOf(this.state.playbackRate);
          if (currentIndex < rates.length - 1) {
            this.handlePlaybackRateChange(rates[currentIndex + 1]);
          }
        } else {
          this.skipTo(playedTime + 1);
        }
        break;
      case 'Comma':
        e.preventDefault();
        if (e.shiftKey) {
          const rates = [0.5, 0.75, 1, 1.25, 1.5, 2];
          const currentIndex = rates.indexOf(this.state.playbackRate);
          if (currentIndex > 0) {
            this.handlePlaybackRateChange(rates[currentIndex - 1]);
          }
        } else {
          this.skipTo(playedTime - 1);
        }
        break;
      case 'KeyF':
        e.preventDefault();
        this.handleToggleFullscreen();
        break;
    }
  }

  initEventListeners() {
    this.videoElem.addEventListener('loadedmetadata', () => {
      this.state.duration = this.videoElem.duration;
      this.placeAnnotationContainer();
      this.videoElem.controls = false;
      this.renderSkipsOnScrubber();
    });

    this.videoElem.addEventListener('timeupdate', () => this.handleProgress());

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
    });

    if (this.controls.playPauseBtn) {
      this.controls.playPauseBtn.addEventListener('click', (e) => {
        e.preventDefault();
        this.togglePlayPause();
      });
    }

    if (this.controls.scrubber) {
      this.controls.scrubber.addEventListener('click', (e) => this.handleSeekClick(e));
    }

    if (this.controls.fullscreenBtn) {
      this.controls.fullscreenBtn.addEventListener('click', () => this.handleToggleFullscreen());
    }

    document.addEventListener('fullscreenchange', () => this.handleFullscreenChange());
    document.addEventListener('webkitfullscreenchange', () => this.handleFullscreenChange());
    document.addEventListener('mozfullscreenchange', () => this.handleFullscreenChange());
    document.addEventListener('MSFullscreenChange', () => this.handleFullscreenChange());

    if (this.controls.container) {
      this.controls.container.addEventListener('mousemove', () => this.handleMouseMoved());
      this.controls.container.addEventListener('mouseenter', () => {
        this.state.hovering = true;
        this.updateControlsVisibility();
      });
      this.controls.container.addEventListener('mouseleave', () => {
        this.state.hovering = false;
        this.updateControlsVisibility();
      });

      const controlButtons = this.controls.container.querySelectorAll('#returnBtn, #reloadJsonBtn, .video-controls');
      controlButtons.forEach(button => {
        button.addEventListener('mouseenter', () => {
          this.state.controlsHovering = true;
          this.updateControlsVisibility();
        });
        button.addEventListener('mouseleave', () => {
          this.state.controlsHovering = false;
          this.updateControlsVisibility();
        });
      });
    }

    this.videoElem.addEventListener('mousemove', () => this.handleMouseMoved());
    this.videoElem.addEventListener('mouseenter', () => {
      this.state.hovering = true;
      this.updateControlsVisibility();
    });
    this.videoElem.addEventListener('mouseleave', () => {
      if (!this.state.controlsHovering) {
        this.state.hovering = false;
        this.updateControlsVisibility();
      }
    });

    this.annotationContainer.addEventListener('mousemove', () => this.handleMouseMoved());
    this.annotationContainer.addEventListener('mouseenter', () => {
      this.state.hovering = true;
      this.updateControlsVisibility();
    });
    this.annotationContainer.addEventListener('mouseleave', () => {
      if (!this.state.controlsHovering) {
        this.state.hovering = false;
        this.updateControlsVisibility();
      }
    });

    document.addEventListener('keydown', (e) => this.handleKeydown(e));

    window.addEventListener('resize', () => this.placeAnnotationContainer());
    this.videoElem.addEventListener('resize', () => this.placeAnnotationContainer());
  }
}
