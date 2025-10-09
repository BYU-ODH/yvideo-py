// Y-video Core Player Main JS

// This file should NEVER directly manipulate AnnotationPlayer or SubtitleSidebar.
// All interactions should go through the player object's exposed API.


import { AnnotationPlayer } from './AnnotationPlayer.js';

(function() {
    'use strict';

    let annotationPlayer = null;

    function init() {
        const container = document.querySelector('.annotation-player-container');

        if (!container) {
            console.error('Player container not found');
            return;
        }

        // Prepare tracks array from subtitle data
        let tracks = [];
        if (window.playerData && window.playerData.subtitles) {
            const subtitles = window.playerData.subtitles;
            // Handle single subtitle object or array
            const subtitleArray = Array.isArray(subtitles) ? subtitles : [subtitles];
            tracks = subtitleArray.filter(sub => sub.vtt || sub.url);
        }

        // Get clips data if available
        let clips = [];
        if (window.playerData && window.playerData.clips) {
            clips = window.playerData.clips;
        }

        const enableSubtitleSidebar = tracks.length > 0;

        annotationPlayer = new AnnotationPlayer({
            container: container,
            disabledControls: [],
            tracks: tracks,
            clips: clips,
            subtitleSidebar: enableSubtitleSidebar
        });

        // Load annotation data if available
        if (window.playerData) {
            const data = {
                annotations: window.playerData.events || [],
            };
            annotationPlayer.loadData(data);
        }

        // Expose to window for debugging
        window.videoPlayer = annotationPlayer;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
