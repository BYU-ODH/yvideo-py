class SubtitleEditor {
  constructor() {
    this.videoEl = null;
    this.trackSelectorEl = null;
    this.cuesDisplayEl = null;
    this.cueEditorEl = null;
    this.displayCues = null;
    this.editCues = null;
    this._initialize();
  }

  toggleCueDisplay(e) {
    function findEditableCue(element) {
      if (element == null) {
        return null;
      }
      const cssClasses = element.classList;
      for (let cssClass of cssClasses) {
        if (cssClass == "editable-cue") {
          return element;
        }
      }
      return findEditableCue(element.parentElement);
    }

    e.preventDefault();
    const cueContainer = findEditableCue(this)
    const cue = cueContainer.querySelector(".cue-display");
    const cueForm = cueContainer.querySelector(".cue-edit");
    cue.classList.toggle("hidden");
    cueForm.classList.toggle("hidden");
  }

  _setupCueEditingEventListeners() {
    this.displayCues = document.getElementsByClassName("cue-display");
    this.editCues = document.getElementsByClassName("cancel-cue-edit-button");

    for (let displayCue of this.displayCues) {
      displayCue.addEventListener("click", this.toggleCueDisplay);
    }

    for (let editCue of this.editCues) {
      editCue.addEventListener("click", this.toggleCueDisplay);
    }
  }


  // METHODS FOR SUBTITLE TRACKS
  _getSelectedTrackInfo() {
    const options = this.trackSelectorEl.children;
    let name = "";
    let id = null;
    for (let option of options) {
      if (option.selected && option.dataset["name"]) {
        name = option.dataset["name"];
        id = option.value;
      }
    }

    return {
      "name": name,
      "id": id
    }
  }

  async _switchSubtitleTrack() {
    const trackInfo = this._getSelectedTrackInfo();
    const trackName = trackInfo["name"];
    if (trackName) {
      const trackId = Number(trackInfo["id"]);
      const cuesResponse = await fetch(`/subtitle-editor/get-editable-subtitles/${trackId}/`);
      this.cuesDisplayEl.innerHTML = await cuesResponse.text();
      this._setupCueEditingEventListeners();
    }
  }

  // METHODS FOR SUBTITLE CUES
  _secondsToHMS(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${hours}:${minutes}:${secs}`;
  }

  _hmsToSeconds(hmsString) {
    const parts = hmsString.split(':');
    if (parts.length == 3) {
      return Number(parts[0] * 3600) + Number(parts[1] * 60) + Number(parts[2]);
    }
    return null;
  }

  _initialize() {
    this.videoEl = document.getElementById("video-player");
    this.trackSelectorEl = document.getElementById("subtitle-track-selector");
    this.cuesDisplayEl = document.getElementById("editable-cues");
    this.cueEditorEl = document.getElementById("subtitle-cue-editor");

    if (this.trackSelectorEl) {
      this.trackSelectorEl.addEventListener("change", this._switchSubtitleTrack.bind(this));
    }
  }
}

new SubtitleEditor();
