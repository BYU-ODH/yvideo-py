import { AnnotationPlayer } from './AnnotationPlayer.js';

(function() {
    'use strict';

    let annotationPlayer = null;

    function init() {
        const container = document.getElementById('player-container');

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

        const enableSubtitleSidebar = window.playerData && window.playerData.hasSubtitles && tracks.length > 0;

        annotationPlayer = new AnnotationPlayer({
            container: container,
            disabledControls: [],
            tracks: tracks,
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
