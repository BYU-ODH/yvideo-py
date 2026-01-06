// Y-video Core Player Main JS

// This file should NEVER directly manipulate AnnotationPlayer or SubtitleSidebar.
// All interactions should go through the player object's exposed API.


import { AnnotationPlayer } from './AnnotationPlayer.js';

(function() {
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
        const playerData = await (await fetch("/player-data/" + contentId + '/', {
            method: "POST",
            headers: {"X-CSRFToken": csrfToken},
            mode: "same-origin"
        })).json();

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
})();
