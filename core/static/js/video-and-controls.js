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

        annotationPlayer = new AnnotationPlayer({
            container: container,
            disabledControls: [] // Can customize which controls to disable
        });

        // Load player data if available
        if (window.playerData) {
            const data = {
                annotations: window.playerData.events || [],
                subtitles: window.playerData.subtitles || [],
            };
            annotationPlayer.loadData(data);
        }

        // Expose to window for debugging
        window.videoPlayer = annotationPlayer;

        // Set up Django-specific integrations
        setupDjangoIntegration();
    }

    function setupDjangoIntegration() {
        // Add additional event listeners or customizations specific to Django app
        // For example, transcript sidebar toggle or other UI elements

        const transcriptBtn = annotationPlayer.controls.transcriptBtn;
        if (transcriptBtn) {
            transcriptBtn.addEventListener('click', () => {
                const transcriptSidebar = document.getElementById('transcript-sidebar');
                if (transcriptSidebar) {
                    transcriptSidebar.classList.toggle('hidden');
                    annotationPlayer.placeAnnotationContainer();
                }
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
