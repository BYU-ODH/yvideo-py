export class AnnotationPlayer {
  constructor(videoElem, annotationContainer, options = {}) {
    this.videoElem = videoElem;
    this.annotationContainer = annotationContainer;
    this.annotations = [];
    this.currently = { muting: -1, blanking: -1, blurring: -1 };
    this.paused = true;
    this.controlsTimeout = null;
    this.timeCache = 0;
    this.censors = [];
    this.initEventListeners();
    // Position annotation container initially
    this.placeAnnotationContainer();
  }

  getVideoDimensions() {
    // Ratio of the video media's intrinsic dimensions
    var videoRatio = this.videoElem.videoWidth / this.videoElem.videoHeight;

    // The width and height of the video element
    var width = this.videoElem.offsetWidth;
    var height = this.videoElem.offsetHeight;

    // The ratio of the element's width to its height
    var elementRatio = width / height;

    // If the video element is short and wide
    if (elementRatio > videoRatio) {
      width = height * videoRatio;
      // It must be tall and thin, or exactly equal to the original ratio
    } else {
      height = width / videoRatio;
    }
    return {
      width: width,
      height: height,
    };
  }

  placeAnnotationContainer() {
    // Get the bounding rect of the video element
    const videoRect = this.videoElem.getBoundingClientRect();

    // Intrinsic video size
    const videoWidth = this.videoElem.videoWidth;
    const videoHeight = this.videoElem.videoHeight;
    if (!videoWidth || !videoHeight) {
      // Video metadata not loaded yet
      return;
    }

    // Displayed element size
    const elemWidth = this.videoElem.clientWidth;
    const elemHeight = this.videoElem.clientHeight;

    // Calculate aspect ratios
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

    // Position annotation container absolutely over the video media
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
      // Defensive: ensure required fields exist
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

  loadAnnotations(annotationData) {
    this.annotations = [];
    const jsonObj =
      typeof annotationData === "string"
        ? JSON.parse(annotationData)
        : annotationData;
    if (jsonObj["media"]) {
      this.annotations = this.parseHummediaAnnotations(jsonObj);
    } else if (jsonObj[0]["options"]) {
      this.annotations = this.parseICLegacyAnnotations(jsonObj);
    } else if (jsonObj[0]["type"] && jsonObj[0]["start"] && jsonObj[0]["end"]) {
      // TODO ensure this is robust
      this.annotations = this.parseYvideoV1Annotations(jsonObj);
    } else {
      console.error("Unsupported annotation format:", jsonObj);
      return;
    }
    this.annotate();
    this.censors = [];
    for (let i = 0; i < this.annotations.length; i++) {
      if (this.annotations[i].type === "censor") {
        let censor = [];
        censor[0] = this.annotations[i].start;
        censor[1] = this.annotations[i].end;
        censor[2] = [];
        Object.entries(this.annotations[i].details.position).forEach(
          ([key, val]) => {
            censor[2].push([key, val[0], val[1]]);
          },
        );
        this.censors.push(censor);
      }
    }
  }

  play() {
    this.videoElem.play();
    this.paused = false;
    this.videoElem.controls = false;
    // Hide controls and returnBtn immediately when playing starts
    const playerContainer = document.getElementById("playerContainer");
    if (playerContainer) playerContainer.classList.add("controls-hidden");
  }

  pause() {
    this.videoElem.pause();
    this.paused = true;
    this.videoElem.controls = true;
    // Show controls and returnBtn when paused
    const playerContainer = document.getElementById("playerContainer");
    if (playerContainer) playerContainer.classList.remove("controls-hidden");
  }

  togglePlayPause() {
    if (this.videoElem.paused) {
      this.play();
    } else {
      this.pause();
    }
  }

  skipTo(time) {
    this.videoElem.controls = false;
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
    // Optionally add style for blanked video
  }

  unblank() {
    this.videoElem.classList.remove("blanked");
  }

  blur() {
    this.videoElem.classList.add("blurred");
    // Optionally add style for blurred video
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

  initEventListeners() {
    // Keyboard/mouse handlers for controls
    this.videoElem.addEventListener("pause", () => {
      this.paused = true;
      if (this.controlsTimeout) clearTimeout(this.controlsTimeout);
      // Show controls and returnBtn when paused
      const playerContainer = document.getElementById("playerContainer");
      if (playerContainer) playerContainer.classList.remove("controls-hidden");
    });
    this.videoElem.addEventListener("play", () => {
      this.paused = false;
      // Hide controls and returnBtn immediately when playing starts
      const playerContainer = document.getElementById("playerContainer");
      if (playerContainer) playerContainer.classList.add("controls-hidden");
    });
    this.videoElem.addEventListener("mousemove", () => {
      this.videoElem.controls = true;
      if (this.controlsTimeout) clearTimeout(this.controlsTimeout);
      // Show controls and returnBtn on mousemove
      const playerContainer = document.getElementById("playerContainer");
      if (playerContainer) playerContainer.classList.remove("controls-hidden");
      if (!this.videoElem.paused) {
        this.controlsTimeout = setTimeout(() => {
          this.videoElem.controls = false;
          // Hide controls and returnBtn after timeout
          if (playerContainer) playerContainer.classList.add("controls-hidden");
        }, 3000);
      }
    });
    document.addEventListener("keyup", (e) => {
      if (e.key === " " || e.code === "Space") {
        this.togglePlayPause();
        this.timeCache = this.videoElem.currentTime;
      }
    });
    document.addEventListener("keydown", (e) => {
      // Right arrow
      if (e.key === "ArrowRight" || e.code === "ArrowRight") {
        if (this.videoElem.paused) {
          this.skipTo(this.timeCache + 0.1);
        } else {
          this.skipTo(this.timeCache + 5);
        }
      }
      // Left arrow
      else if (e.key === "ArrowLeft" || e.code === "ArrowLeft") {
        if (this.videoElem.paused) {
          this.skipTo(this.timeCache - 0.1);
        } else {
          this.skipTo(this.timeCache - 5);
        }
      }
    });
    // Maintain annotation container position on resize and video metadata load
    window.addEventListener("resize", () => {
      this.placeAnnotationContainer();
    });
    this.videoElem.addEventListener("loadedmetadata", () => {
      this.placeAnnotationContainer();
    });
    this.videoElem.addEventListener("resize", () => {
      this.placeAnnotationContainer();
    });
  }
}
