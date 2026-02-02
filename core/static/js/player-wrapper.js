// Y-video Core Player Main JS

// This file should NEVER directly manipulate AnnotationPlayer or SubtitleSidebar.
// All interactions should go through the player object's exposed API.


import { AnnotationPlayer } from './AnnotationPlayer.js';

function attachAnnotationPlayer() {
    'use strict';

    let annotationPlayer = null;

    async function init() {
        const container = document.querySelector('.annotation-player-container');
        if (!container) {
            console.error('Player container not found');
            return;
        }

        // fetch player subtitles, annotations, and clips
        const contentId = container.dataset.contentid;
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
        const playerDataResponse = await fetch("/player-data/" + contentId + '/', {
            method: "POST",
            headers: {"X-CSRFToken": csrfToken},
            mode: "same-origin"
        });
        const playerData = await playerDataResponse.json();

        let tracks = [];
        if (playerData && playerData.subtitleTracks) {
            const subtitles = playerData.subtitleTracks;
            const subtitleArray = Array.isArray(subtitles) ? subtitles : [subtitles];
            tracks = subtitleArray.filter(sub => sub.vtt || sub.url);
        }

        let clips = [];
        if (playerData && playerData.clips) {
            clips = playerData.clips;
        }

        const enableSubtitleSidebar = tracks.length > 0;

        annotationPlayer = new AnnotationPlayer({
            container: container,
            disabledControls: [],
            tracks: tracks,
            clips: clips,
            subtitleSidebar: enableSubtitleSidebar
        });

        if (playerData) {
            const data = {
                annotations: playerData.annotations || [],
            };
            annotationPlayer.loadData(data);
        }

        window.videoPlayer = annotationPlayer;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
}

attachAnnotationPlayer();

// watch for changes in video section. reload annotation player if changes occur
function handleVideoSectionChanges(mutationList) {
  for (let mutation of mutationList) {
    if (mutation.type == "childList") {
      attachAnnotationPlayer();
    }
  }
}

const videoSectionObserver = new MutationObserver(handleVideoSectionChanges);
videoSectionObserver.observe(document.getElementById("video-section"), { childList: true });
