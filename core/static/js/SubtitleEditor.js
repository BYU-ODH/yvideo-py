class SubtitleEditor {
    constructor() {
        this.trackSelectorEl = null;
        this.videoEl = null;
        this.tracks = [];

        this._initialize();
    }

    _disableAllTracks() {
        for (let track of this.tracks) {
            track.mode = "disabled";
        }
    }

    _enableTrack(trackName) {
        this._disableAllTracks();
        if (trackName) {
            for (let track of this.tracks) {
                if (track.label == trackName) {
                    track.mode = "showing";
                    return;
                }
            }
        }
    }

    _checkIfTrackExists(label) {
        for (let track of this.tracks) {
            if (track.label == label) {
                return true;
            }
        }
        return false;
    }

    _createNewTrack(name, trackValue) {
        const newTrack = this.videoEl.addTextTrack(trackValue.kind, name, trackValue.srclang);
        for (let cue of trackValue.cues) {
            const newVttCue = new VTTCue(cue.start, cue.end, cue.text);
            newTrack.addCue(newVttCue);
        }
        this.tracks.push(newTrack);
    }

    _getSelectedTrackInfo() {
        const options = this.trackSelectorEl.children;
        let name = "";
        let info = null;
        for (let option of options) {
            if (option.selected && option.dataset["name"]) {
                name = option.dataset["name"];
                info = option.value;
            }
        }

        return {
            "name": name,
            "value": info
        }
    }

    _switchSubtitleTrack() {
        const trackInfo = this._getSelectedTrackInfo();
        const trackName = trackInfo["name"];
        const trackExists = this._checkIfTrackExists(trackName);
        if (!trackExists && trackName) {
            const trackValue = JSON.parse(trackInfo["value"]);
            this._createNewTrack(trackName, trackValue)
        }
        this._enableTrack(trackName);
    }

    _initialize() {
        this.videoEl = document.getElementById("video-player");
        this.trackSelectorEl = document.getElementById("subtitle-track-selector");
        this.trackSelectorEl.addEventListener("change", this._switchSubtitleTrack.bind(this));
    }
}

new SubtitleEditor();
