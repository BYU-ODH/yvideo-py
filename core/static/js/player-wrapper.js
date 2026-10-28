// Y-video Core Player Main JS

// This file should NEVER directly manipulate AnnotationPlayer or SubtitleSidebar.
// All interactions should go through the player object's exposed API.


import { AnnotationPlayer } from './AnnotationPlayer.js';
import { getCSRFToken } from './utils.js';

async function getPlayerData(contentId) {
  const playerDataResponse = await fetch(`/content/${contentId}/player-data/`, {
      method: "POST",
      headers: {"X-CSRFToken": getCSRFToken()},
      mode: "same-origin"
  });
  if (!playerDataResponse.ok) {
    return false;
  }
  return await playerDataResponse.json();
}

function subtitleTracksFor(videoElem, playerData) {
  if (videoElem.tagName === 'YOUTUBE-VIDEO' || !playerData.subtitleTracks) {
    return [];
  }
  const subtitles = playerData.subtitleTracks;
  const subtitleArray = Array.isArray(subtitles) ? subtitles : [subtitles];
  return subtitleArray.filter(sub => sub.vtt || sub.url);
}

let playerReady;

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

        const videoElem = container.querySelector('#video-player');
        const tracks = subtitleTracksFor(videoElem, playerData);

        let clips = [];
        if (playerData && playerData.clips) {
            clips = playerData.clips;
        }

        const enableSubtitleSidebar = tracks.length > 0;

        annotationPlayer = new AnnotationPlayer({
            container: container,
            video: videoElem,
            disabledControls: [],
            tracks: tracks,
            clips: clips,
            subtitleSidebar: enableSubtitleSidebar,
            allowFastPlayback: playerData.allowFastPlayback !== false,
            clipsOnly: playerData.clipsOnly === true,
            editorMode: container.dataset.editorMode === 'true'
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
        playerReady = new Promise((resolve) => {
            document.addEventListener('DOMContentLoaded', () => resolve(init()));
        });
    } else {
        playerReady = init();
    }
}

attachAnnotationPlayer();

// watch for changes in video section. reload annotation player if changes occur

let latestRefreshId = 0;

async function handleVideoSectionChanges() {
  await playerReady;
  const player = window.videoPlayer;
  if (!player) return;

  // Saves often land in quick succession; only the newest refresh is applied so a slower, older
  // response cannot overwrite the player with stale data.
  const refreshId = ++latestRefreshId;
  const contentId = player.container.dataset["contentid"];
  const playerData = await getPlayerData(contentId);
  if (playerData === false || refreshId !== latestRefreshId) return;

  player.loadData({
    annotations: playerData.annotations || [],
    clips: playerData.clips || [],
    subtitles: subtitleTracksFor(player.videoElem, playerData),
  });
}

function listenForChangesToAnnotations() {
  window.addEventListener("annotationUpdated", async () => {
    await handleVideoSectionChanges()
  });
}

listenForChangesToAnnotations();
