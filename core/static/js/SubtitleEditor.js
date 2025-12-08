class SubtitleEditor {
    constructor() {
        this.videoEl = null;
        this.trackSelectorEl = null;
        this.cuesDisplayEl = null;
        this.cueEditorEl = null;

        this._initialize();
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
          const cuesHTML = await fetch(`subtitle-editor/get-editable-subtitles/${trackId}/`);
          this.cuesDisplayEl.innerHTML = cuesHTML;
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
