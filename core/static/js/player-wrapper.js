// Y-video Core Player Main JS

// This file should NEVER directly manipulate AnnotationPlayer or SubtitleSidebar.
// All interactions should go through the player object's exposed API.


import { AnnotationPlayer } from './AnnotationPlayer.js';

async function getPlayerData(contentId) {
  const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
  const playerDataResponse = await fetch("/player-data/" + contentId + '/', {
      method: "POST",
      headers: {"X-CSRFToken": csrfToken},
      mode: "same-origin"
  });
  if (!playerDataResponse.ok) {
    return false;
  }
  return await playerDataResponse.json();
}

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
        const playerData = await getPlayerData(contentId);
        if (playerData === false) {
          return;
        }

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

async function handleVideoSectionChanges() {
  const player = window.videoPlayer;
  const contentId = player.container.dataset["contentid"];
  const playerData = await getPlayerData(contentId);
  if (playerData !== false) {
    player.loadData({
      annotations: playerData.annotations || []
    });
  }
}

function listenForChangesToAnnotations() {
  window.addEventListener("annotationUpdated", async () => {
    await handleVideoSectionChanges()
  });
}

listenForChangesToAnnotations();
