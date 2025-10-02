import { AnnotationPlayer } from './annotation-player.js';

(function() {
    'use strict';

    let annotationPlayer = null;

    function init() {
        const videoElem = document.getElementById('main-video');
        const container = document.getElementById('player-container');

        if (!videoElem || !container) {
            console.error('Required video elements not found');
            return;
        }

        annotationPlayer = new AnnotationPlayer({
            video: videoElem,
            annotationContainer: '#video-overlay',
            controls: {
                container: '#player-container',
                playButton: '#play-button',
                playPauseBtn: '#play-pause-btn',
                scrubber: '#scrubber',
                scrubberProgress: '#scrubber-progress',
                scrubberDot: '#scrubber-dot',
                playTime: '#play-time',
                fullscreenBtn: '#fullscreen-btn',
                speedBtn: '#speed-btn',
                captionsBtn: '#captions-btn',
                transcriptBtn: '#transcript-btn',
                subtitleText: '#subtitle-text',
            }
        });

        if (window.playerData) {
            const data = {
                annotations: window.playerData.events || [],
                subtitles: window.playerData.subtitles || [],
            };
            annotationPlayer.loadData(data);
            loadSkipEvents(data.annotations);
        }

        bindDjangoSpecificControls();
        checkBrowser();

        window.videoPlayer = annotationPlayer;
    }

    function bindDjangoSpecificControls() {
        const startOverBtn = document.getElementById('start-over');
        if (startOverBtn) {
            startOverBtn.addEventListener('click', () => annotationPlayer.skipTo(0));
        }

        const speedBtn = document.getElementById('speed-btn');
        const speedModal = document.getElementById('speed-modal');
        if (speedBtn && speedModal) {
            speedBtn.addEventListener('click', () => {
                speedModal.classList.toggle('hidden');
                const captionsModal = document.getElementById('captions-modal');
                if (captionsModal) captionsModal.classList.add('hidden');
            });
        }

        const captionsBtn = document.getElementById('captions-btn');
        const captionsModal = document.getElementById('captions-modal');
        if (captionsBtn && captionsModal) {
            captionsBtn.addEventListener('click', () => {
                captionsModal.classList.toggle('hidden');
                if (speedModal) speedModal.classList.add('hidden');
            });
        }

        const transcriptBtn = document.getElementById('transcript-btn');
        if (transcriptBtn) {
            transcriptBtn.addEventListener('click', () => {
                const transcriptSidebar = document.getElementById('transcript-sidebar');
                if (transcriptSidebar) {
                    transcriptSidebar.classList.toggle('hidden');
                    annotationPlayer.placeAnnotationContainer();
                }
            });
        }

        document.querySelectorAll('.speed-option').forEach(btn => {
            btn.addEventListener('click', () => {
                annotationPlayer.handlePlaybackRateChange(parseFloat(btn.dataset.speed));
                if (speedModal) speedModal.classList.add('hidden');
            });
        });

        document.querySelectorAll('.caption-option').forEach(btn => {
            btn.addEventListener('click', () => {
                annotationPlayer.handleCaptionChange(btn.dataset.lang);
                if (captionsModal) captionsModal.classList.add('hidden');
            });
        });

        document.addEventListener('click', (e) => {
            if (speedModal && !e.target.closest('#speed-modal') && !e.target.closest('#speed-btn')) {
                speedModal.classList.add('hidden');
            }
            if (captionsModal && !e.target.closest('#captions-modal') && !e.target.closest('#captions-btn')) {
                captionsModal.classList.add('hidden');
            }
        });
    }

    function loadSkipEvents(annotations) {
        const scrubber = document.getElementById('scrubber');
        if (!scrubber || !annotations) return;

        const duration = annotationPlayer.state.duration;
        if (!duration) return;

        const skipEvents = annotations.filter(event =>
            event.type === 'Skip' || event.type === 'skip'
        );

        skipEvents.forEach(event => {
            const startPercent = (parseFloat(event.start) / duration) * 100;
            const endPercent = (parseFloat(event.end) / duration) * 100;

            const skipElement = document.createElement('div');
            skipElement.className = 'skip-event';
            skipElement.style.left = `${startPercent}%`;
            skipElement.style.width = `${endPercent - startPercent}%`;

            scrubber.appendChild(skipElement);
        });
    }

    function checkBrowser() {
        const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
        const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);

        if (isSafari || isIOS) {
            console.warn('Video playback may not work properly on iOS devices or Safari browser.');
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
