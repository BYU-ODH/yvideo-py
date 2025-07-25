// Video Player - Vanilla JavaScript Implementation
(function() {
    'use strict';

    // Player state
    const player = {
        // DOM elements
        video: null,
        container: null,
        controls: null,
        playButton: null,
        playPauseBtn: null,
        playPauseIcon: null,
        scrubber: null,
        scrubberProgress: null,
        scrubberDot: null,
        playTime: null,
        secondsTimeHolder: null,

        // State variables
        playing: false,
        started: false,
        duration: 0,
        currentTime: 0,
        playbackRate: 1.0,
        muted: false,
        fullscreen: false,
        showTranscript: false,
        mouseInactive: false,
        hovering: false,
        controlsHovering: false,

        // Event data
        events: [],
        subtitles: [],
        displaySubtitles: null,
        subtitleTextIndex: null,

        // Timers
        mouseTimer: null,
        iconTimer: null
    };

    // Initialize player when DOM is loaded
    function init() {
        // Get DOM elements
        player.video = document.getElementById('main-video');
        player.container = document.getElementById('player-container');
        player.controls = document.getElementById('player-controls');
        player.playButton = document.getElementById('play-button');
        player.playPauseBtn = document.getElementById('play-pause-btn');
        player.playPauseIcon = document.getElementById('play-pause-icon');
        player.scrubber = document.getElementById('scrubber');
        player.scrubberProgress = document.getElementById('scrubber-progress');
        player.scrubberDot = document.getElementById('scrubber-dot');
        player.playTime = document.getElementById('play-time');
        player.secondsTimeHolder = document.getElementById('seconds-time-holder');

        if (!player.video || !player.container) {
            console.error('Required video elements not found');
            return;
        }

        // Load data from Django context
        if (window.playerData) {
            player.events = window.playerData.events || [];
            player.subtitles = window.playerData.subtitles || [];
        }

        bindEvents();
        checkBrowser();
        handleAspectRatio();
    }

    function bindEvents() {
        // Video events
        player.video.addEventListener('loadedmetadata', handleDuration);
        player.video.addEventListener('timeupdate', handleProgress);
        player.video.addEventListener('play', handlePlay);
        player.video.addEventListener('pause', handlePause);
        player.video.addEventListener('ended', handlePause);
        player.video.addEventListener('click', handlePlayPause);

        // Control events
        player.playButton.addEventListener('click', handlePlayPause);
        player.playPauseBtn.addEventListener('click', handlePlayPause);
        player.scrubber.addEventListener('click', handleSeekClick);

        // Control buttons
        document.getElementById('start-over').addEventListener('click', () => handleSeek(0));
        document.getElementById('fullscreen-btn').addEventListener('click', handleToggleFullscreen);
        document.getElementById('speed-btn').addEventListener('click', handleToggleSpeedModal);
        document.getElementById('captions-btn').addEventListener('click', handleToggleCaptionsModal);
        document.getElementById('transcript-btn').addEventListener('click', handleToggleTranscript);

        // Speed modal buttons
        const speedOptions = document.querySelectorAll('.speed-option');
        speedOptions.forEach(btn => {
            btn.addEventListener('click', () => handlePlaybackRateChange(parseFloat(btn.dataset.speed)));
        });

        // Caption modal buttons
        const captionOptions = document.querySelectorAll('.caption-option');
        captionOptions.forEach(btn => {
            btn.addEventListener('click', () => handleCaptionChange(btn.dataset.lang));
        });

        // Mouse events
        player.container.addEventListener('mousemove', handleMouseMoved);
        player.container.addEventListener('mouseenter', () => { player.hovering = true; updateControlsVisibility(); });
        player.container.addEventListener('mouseleave', () => { player.hovering = false; updateControlsVisibility(); });
        player.controls.addEventListener('mouseenter', () => { player.controlsHovering = true; updateControlsVisibility(); });
        player.controls.addEventListener('mouseleave', () => { player.controlsHovering = false; updateControlsVisibility(); });

        // Keyboard events
        document.addEventListener('keydown', handleKeydown);

        // Fullscreen events
        document.addEventListener('fullscreenchange', exitFullscreenHandler);
        document.addEventListener('webkitfullscreenchange', exitFullscreenHandler);
        document.addEventListener('mozfullscreenchange', exitFullscreenHandler);
        document.addEventListener('MSFullscreenChange', exitFullscreenHandler);

        // Modal close events
        document.addEventListener('click', handleDocumentClick);

        // Window resize
        window.addEventListener('resize', handleAspectRatio);
    }

    function handleDuration() {
        player.duration = player.video.duration;
        handleAspectRatio();
        loadSkipEvents();
    }

    function handleProgress() {
        player.currentTime = player.video.currentTime;
        const played = player.currentTime / player.duration;

        updateTimeDisplay();
        updateScrubber(played);
        handleSubtitles();
        handleEvents();

        player.secondsTimeHolder.textContent = player.currentTime;
    }

    function updateTimeDisplay() {
        const hours = Math.floor(player.currentTime / 3600);
        const minutes = Math.floor((player.currentTime % 3600) / 60);
        const seconds = Math.floor(player.currentTime % 60);

        const timeString = `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        player.playTime.textContent = timeString;
        player.playTime.classList.add('active');
    }

    function updateScrubber(played) {
        player.scrubberProgress.style.width = `${played * 100}%`;
        player.scrubberDot.style.left = `calc(${played * 100}% - 3px)`;
    }

    function handlePlay() {
        player.playing = true;
        player.started = true;
        player.playButton.classList.add('hidden');
        player.playPauseBtn.classList.add('playing');
    }

    function handlePause() {
        player.playing = false;
        player.playPauseBtn.classList.remove('playing');
    }

    function handlePlayPause() {
        if (player.playing) {
            player.video.pause();
        } else {
            player.video.play();
        }
    }

    function handleSeekClick(e) {
        const rect = player.scrubber.getBoundingClientRect();
        const percent = (e.clientX - rect.left) / rect.width;
        const newTime = percent * player.duration;
        handleSeek(newTime);
    }

    function handleSeek(time) {
        player.video.currentTime = Math.max(0, Math.min(time, player.duration));
        reactivateEvents(time);
    }

    function handlePlaybackRateChange(rate) {
        player.playbackRate = rate;
        player.video.playbackRate = rate;

        // Update UI
        document.querySelector('#speed-btn .speed-text').textContent = `${rate}x`;

        document.querySelectorAll('.speed-option').forEach(btn => {
            btn.classList.remove('active-value');
            if (parseFloat(btn.dataset.speed) === rate) {
                btn.classList.add('active-value');
            }
        });

        document.getElementById('speed-modal').classList.add('hidden');
    }

    function handleCaptionChange(lang) {
        // Update caption settings
        document.querySelectorAll('.caption-option').forEach(btn => {
            btn.classList.remove('active-value');
            if (btn.dataset.lang === lang) {
                btn.classList.add('active-value');
            }
        });

        // Set display subtitles
        if (lang === 'off') {
            player.displaySubtitles = null;
        } else {
            player.displaySubtitles = player.subtitles.find(sub => sub.language === lang);
        }

        document.getElementById('captions-modal').classList.add('hidden');
    }

    function handleToggleFullscreen() {
        const elem = player.container;

        if (!player.fullscreen) {
            if (elem.requestFullscreen) {
                elem.requestFullscreen();
            } else if (elem.mozRequestFullScreen) {
                elem.mozRequestFullScreen();
            } else if (elem.webkitRequestFullscreen) {
                elem.webkitRequestFullscreen();
            } else if (elem.msRequestFullscreen) {
                elem.msRequestFullscreen();
            }
        } else {
            if (document.exitFullscreen) {
                document.exitFullscreen();
            } else if (document.mozCancelFullScreen) {
                document.mozCancelFullScreen();
            } else if (document.webkitExitFullscreen) {
                document.webkitExitFullscreen();
            } else if (document.msExitFullscreen) {
                document.msExitFullscreen();
            }
        }

        player.fullscreen = !player.fullscreen;
        document.getElementById('fullscreen-btn').classList.toggle('fullscreen', player.fullscreen);
    }

    function exitFullscreenHandler() {
        if (!document.fullscreenElement && !document.webkitIsFullScreen &&
            !document.mozFullScreen && !document.msFullscreenElement) {
            if (player.fullscreen) {
                player.fullscreen = false;
                document.getElementById('fullscreen-btn').classList.remove('fullscreen');
            }
        }
    }

    function handleToggleSpeedModal() {
        document.getElementById('speed-modal').classList.toggle('hidden');
        document.getElementById('captions-modal').classList.add('hidden');
    }

    function handleToggleCaptionsModal() {
        document.getElementById('captions-modal').classList.toggle('hidden');
        document.getElementById('speed-modal').classList.add('hidden');
    }

    function handleToggleTranscript() {
        player.showTranscript = !player.showTranscript;
        document.getElementById('transcript-sidebar').classList.toggle('hidden', !player.showTranscript);
        handleAspectRatio();
    }

    function handleMouseMoved() {
        player.mouseInactive = false;
        player.container.classList.remove('cursor-hidden');
        updateControlsVisibility();

        if (player.mouseTimer) clearTimeout(player.mouseTimer);
        player.mouseTimer = setTimeout(() => {
            player.mouseInactive = true;
            player.container.classList.add('cursor-hidden');
            updateControlsVisibility();
        }, 3000);
    }

    function updateControlsVisibility() {
        const shouldShow = (!player.mouseInactive && player.hovering) || !player.playing || player.controlsHovering;
        player.controls.classList.toggle('hidden', !shouldShow);
    }

    function handleKeydown(e) {
        const playedTime = player.currentTime;

        switch (e.code) {
            case 'ArrowRight':
                e.preventDefault();
                handleSeek(playedTime + 10);
                break;
            case 'ArrowLeft':
                e.preventDefault();
                handleSeek(playedTime - 10);
                break;
            case 'Period':
                e.preventDefault();
                if (!e.shiftKey) {
                    handleSeek(playedTime + 1);
                } else {
                    const rates = [0.5, 0.75, 1, 1.25, 1.5, 2];
                    const currentIndex = rates.indexOf(player.playbackRate);
                    if (currentIndex < rates.length - 1) {
                        handlePlaybackRateChange(rates[currentIndex + 1]);
                    }
                }
                break;
            case 'Comma':
                e.preventDefault();
                if (!e.shiftKey) {
                    handleSeek(playedTime - 1);
                } else {
                    const rates = [0.5, 0.75, 1, 1.25, 1.5, 2];
                    const currentIndex = rates.indexOf(player.playbackRate);
                    if (currentIndex > 0) {
                        handlePlaybackRateChange(rates[currentIndex - 1]);
                    }
                }
                break;
            case 'Space':
                e.preventDefault();
                handlePlayPause();
                break;
            case 'KeyF':
                e.preventDefault();
                handleToggleFullscreen();
                break;
            case 'KeyC':
                e.preventDefault();
                handleToggleTranscript();
                break;
        }
    }

    function handleDocumentClick(e) {
        if (!e.target.closest('#speed-modal') && !e.target.closest('#speed-btn')) {
            document.getElementById('speed-modal').classList.add('hidden');
        }
        if (!e.target.closest('#captions-modal') && !e.target.closest('#captions-btn')) {
            document.getElementById('captions-modal').classList.add('hidden');
        }
    }

    function handleSubtitles() {
        if (!player.displaySubtitles || !player.displaySubtitles.content) return;

        const subtitleElement = document.getElementById('subtitle-text');

        for (let i = 0; i < player.displaySubtitles.content.length; i++) {
            const subtitle = player.displaySubtitles.content[i];

            if (player.currentTime >= subtitle.start && player.currentTime <= (subtitle.end || subtitle.start + 5)) {
                subtitleElement.textContent = subtitle.text;
                player.subtitleTextIndex = i;
                return;
            }
        }

        subtitleElement.textContent = '';
        player.subtitleTextIndex = null;
    }

    function handleEvents() {
        if (!player.events || player.events.length === 0) return;

        player.events.forEach(event => {
            if (!event.active) return;

            const startTime = parseFloat(event.start);
            const endTime = parseFloat(event.end || event.start);

            if (player.currentTime >= startTime && player.currentTime <= endTime) {
                executeEvent(event);
            } else if (player.currentTime > endTime && event.type === 'Mute' && player.muted) {
                player.muted = false;
                player.video.muted = false;
                event.active = false;
            }
        });
    }

    function executeEvent(event) {
        switch (event.type) {
            case 'Mute':
                if (!player.muted) {
                    player.muted = true;
                    player.video.muted = true;
                }
                break;
            case 'Pause':
                event.active = false;
                player.video.pause();
                if (event.message) {
                    showPauseMessage(event.message);
                }
                break;
            case 'Skip':
                event.active = false;
                handleSeek(parseFloat(event.end));
                break;
        }
    }

    function showPauseMessage(message) {
        const pauseMessage = document.getElementById('pause-message');
        pauseMessage.innerHTML = message + '<button type="button" onclick="this.parentElement.style.visibility=\'hidden\'">Close</button>';
        pauseMessage.style.visibility = 'visible';
    }

    function loadSkipEvents() {
        const skipEvents = player.events.filter(event => event.type === 'Skip');

        skipEvents.forEach(event => {
            const startPercent = (parseFloat(event.start) / player.duration) * 100;
            const endPercent = (parseFloat(event.end) / player.duration) * 100;

            const skipElement = document.createElement('div');
            skipElement.className = 'skip-event';
            skipElement.style.left = `${startPercent}%`;
            skipElement.style.width = `${endPercent - startPercent}%`;

            player.scrubber.appendChild(skipElement);
        });
    }

    function reactivateEvents(seekTime) {
        player.events.forEach(event => {
            const endTime = parseFloat(event.end || event.start);
            if (seekTime < endTime && !event.active) {
                event.active = true;
            }
        });
    }

    function handleAspectRatio() {
        const container = player.container;
        const video = player.video;
        const overlay = document.getElementById('video-overlay');

        if (!container || !video || !overlay) return;

        const containerWidth = container.offsetWidth;
        const containerHeight = container.offsetHeight;
        const aspectRatio = [16, 9];

        if (containerWidth / containerHeight > aspectRatio[0] / aspectRatio[1]) {
            const videoWidth = containerHeight * (aspectRatio[0] / aspectRatio[1]);
            const padding = (containerWidth - videoWidth) / 2;

            overlay.style.left = `${padding}px`;
            overlay.style.top = '0px';
            overlay.style.width = `${videoWidth}px`;
            overlay.style.height = `${containerHeight}px`;
        } else {
            const videoHeight = containerWidth * aspectRatio[1] / aspectRatio[0];
            const padding = (containerHeight - videoHeight) / 2;

            overlay.style.left = '0px';
            overlay.style.top = `${padding}px`;
            overlay.style.width = `${containerWidth}px`;
            overlay.style.height = `${videoHeight}px`;
        }
    }

    function checkBrowser() {
        const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
        const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);

        if (isSafari || isIOS) {
            console.warn('Video playback may not work properly on iOS devices or Safari browser.');
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose player for external access
    window.videoPlayer = player;

})();
