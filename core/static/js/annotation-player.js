export class AnnotationPlayer {
  constructor(options = {}) {
    this.videoElem = this._getElement(options.video);
    this.annotationContainer = this._getElement(options.annotationContainer);

    this.controls = {
      container: this._getElement(options.controls?.container),
      playButton: this._getElement(options.controls?.playButton),
      playPauseBtn: this._getElement(options.controls?.playPauseBtn),
      scrubber: this._getElement(options.controls?.scrubber),
      scrubberProgress: this._getElement(options.controls?.scrubberProgress),
      scrubberDot: this._getElement(options.controls?.scrubberDot),
      playTime: this._getElement(options.controls?.playTime),
      fullscreenBtn: this._getElement(options.controls?.fullscreenBtn),
      speedBtn: this._getElement(options.controls?.speedBtn),
      captionsBtn: this._getElement(options.controls?.captionsBtn),
      transcriptBtn: this._getElement(options.controls?.transcriptBtn),
      transcriptContainer: this._getElement(options.controls?.transcriptContainer),
      subtitleText: this._getElement(options.controls?.subtitleText),
    };

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

  _getElement(selector) {
    if (!selector) return null;
    if (selector instanceof HTMLElement) return selector;
    if (typeof selector === 'string') return document.querySelector(selector);
    return null;
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
    if (this.controls.container) this.controls.container.classList.remove("controls-hidden");
  }

  togglePlayPause() {
    if (this.videoElem.paused) {
      this.play();
    } else {
      this.pause();
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

    if (this.controls.fullscreenBtn) {
      this.controls.fullscreenBtn.classList.toggle('fullscreen', this.state.fullscreen);
    }
  }

  handleFullscreenChange() {
    const isFullscreen = !!(document.fullscreenElement || document.webkitIsFullScreen ||
      document.mozFullScreen || document.msFullscreenElement);

    if (this.state.fullscreen !== isFullscreen) {
      this.state.fullscreen = isFullscreen;
      if (this.controls.fullscreenBtn) {
        this.controls.fullscreenBtn.classList.toggle('fullscreen', isFullscreen);
      }
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
    }

    // Make sure built-in video controls are enabled when mouse moves
    if (!this.videoElem.controls) {
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
    if (!this.controls.container) return;

    const shouldShow = (!this.state.mouseInactive && this.state.hovering) ||
                       !this.state.playing ||
                       this.state.controlsHovering;

    this.controls.container.classList.toggle('controls-hidden', !shouldShow);
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
    });

    this.videoElem.addEventListener('timeupdate', () => this.handleProgress());

    this.videoElem.addEventListener('play', () => {
      this.paused = false;
      this.state.playing = true;
      this.state.started = true;

      if (this.controls.playPauseBtn) {
        this.controls.playPauseBtn.classList.add('playing');
      }
      if (this.controls.playButton) {
        this.controls.playButton.classList.add('hidden');
      }
    });

    this.videoElem.addEventListener('pause', () => {
      this.paused = true;
      this.state.playing = false;

      if (this.controls.playPauseBtn) {
        this.controls.playPauseBtn.classList.remove('playing');
      }
    });

    this.videoElem.addEventListener('click', (e) => {
      e.preventDefault();
      this.togglePlayPause();
    });

    if (this.controls.playButton) {
      this.controls.playButton.addEventListener('click', (e) => {
        e.preventDefault();
        this.togglePlayPause();
      });
    }

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

      // Add event listeners for specific buttons to ensure they stay visible when hovered
      const controlButtons = this.controls.container.querySelectorAll('#returnBtn, #reloadJsonBtn');
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

    // Keep these for additional mouse tracking on video and annotation container
    this.videoElem.addEventListener('mousemove', () => this.handleMouseMoved());
    this.videoElem.addEventListener('mouseenter', () => {
      this.state.hovering = true;
      this.updateControlsVisibility();
    });
    this.videoElem.addEventListener('mouseleave', () => {
      // Only set hovering to false if we're not hovering over controls or buttons
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
      // Only set hovering to false if we're not hovering over controls or buttons
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
