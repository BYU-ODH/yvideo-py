class SubtitleEditor {
  constructor() {
    this.videoEl = null;
    this.trackSelectorEl = null;
    this.selectedTrackId = null;
    this.cuesDisplayEl = null;
    this.cueEditorEl = null;
    this.displayCues = null;
    this.editCues = null;
    this.saveCues = null;
    this.cuesAreBeingUpdated = false;
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
    this.saveCues = document.getElementsByClassName("save-cue-edit-button");

    for (let displayCue of this.displayCues) {
      displayCue.addEventListener("click", this.toggleCueDisplay);
    }

    for (let editCue of this.editCues) {
      editCue.addEventListener("click", this.toggleCueDisplay);
    }

    for (let saveCue of this.saveCues) {
      saveCue.addEventListener("click", () => this._saveCues(true))
    }
  }


  _packageCuesAsJSON() {
    const packagedCues = [];
    for (let cue of this.displayCues) {
      let identifier = cue.querySelector(".cue-identifier")?.innerText;
      if (!identifier) {
        identifier = ""
      }

      let type = cue.querySelector(".cue-type")?.innerText;
      if (!type) {
        type = "CUE";
      }

      let payload = cue.querySelector(".cue-payload")?.innerText;
      if (!payload) {
        payload = "";
      }

      let start_time = cue.querySelector(".cue-start-time")?.innerText;
      if (!start_time) {
        start_time = "";
      }

      let end_time = cue.querySelector(".cue-end-time")?.innerText;
      if (!end_time) {
        end_time = "";
      }

      let cue_settings = cue.querySelector(".cue-settings")?.innerText;
      if (!cue_settings) {
        cue_settings = "";
      }

      const cueMap = {
        "identifier": identifier,
        "type": type,
        "payload": payload,
        "start_time": start_time,
        "end_time": end_time,
        "cue_settings": cue_settings
      }
      packagedCues.push(cueMap);
    }

    return JSON.stringify(packagedCues);
  }


  async _saveCues(isAutosave, secondsNudge = 0, nudgeExcludedCues = []) {
    const cues = this._packageCuesAsJSON();
    const result = await fetch("/subtitle-editor/update-subtitle-file", {
      method: "POST",
      headers: {"X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value},
      body: JSON.stringify({ subtitle_id: this.selectedTrackId, cues: cues, is_autosave: isAutosave, seconds_nudge: secondsNudge, nudge_excluded_cues: nudgeExcludedCues })
    });
    if (!result.ok) {
      console.log("Request to update subtitles failed! " + result.status);
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
      this.selectedTrackId = trackId;
      const cuesResponse = await fetch(`/subtitle-editor/get-editable-subtitles/${trackId}/`);
      if (!cuesResponse.ok) {
        this.selectedTrackId = null;
      }
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
