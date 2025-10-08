// International Cinema Player Main JS

// This file should NEVER directly manipulate AnnotationPlayer or SubtitleSidebar.
// All interactions should go through the player object's exposed API.

const fs = window.require("fs");
const { webUtils } = window.require("electron");
import { AnnotationPlayer } from "./AnnotationPlayer.js";

export const player = {
  annotationPlayer: null,
  annotationMode: false,
  icfData: null,
  selectedFiles: null, // Add this to cache the files

  initializeOrSelectFiles: () => {
    const files = player.getSelectedFiles();
    if (!files) {
      document.getElementById("filePicker").click();

      function onFileChange() {
        document.getElementById("files").textContent =
          player.getSelectedFiles().icfFile.name;
        document.getElementById("playButton").classList.add("ready");
        document
          .getElementById("filePicker")
          .removeEventListener("change", onFileChange);

        document.getElementById("playButton").onclick = () => {
          player.startPlayer();
        };
      }
      document
        .getElementById("filePicker")
        .addEventListener("change", onFileChange);
    } else {
      player.startPlayer();
    }
  },

  toggleAnnotationMode: () => {
    player.annotationMode = !player.annotationMode;

    // Update button appearance to match state
    const toggleBtn = document.getElementById("toggleAnnotationModeBtn");
    if (player.annotationMode) {
      toggleBtn.classList.add("active");
    } else {
      toggleBtn.classList.remove("active");
    }
    if (window.toggleDevTools) {
      try {
        window.toggleDevTools();
      } catch (e) {
        console.warn("Failed to toggle dev tools:", e);
      }
    }
  },

  startPlayer: () => {
    const files = player.getSelectedFiles();
    if (!files) {
      alert(
        "Error: The selected folder does not contain an *.icf file. Please try again.",
      );
      return;
    }
    player.selectedFiles = files; // Cache the files
    player.parseAndPlay(files["annotationFile"], player.initializePlayerAndPlay);
  },

  hidePlayer: () => {
    document.getElementById("splashScreen").style.display = "block";
    document.querySelector(".main-content").classList.remove("active");
    document.querySelector(".annotation-player-container").style.display = "none";
    document.getElementById("playButton").classList.remove("ready");
    document.getElementById("reloadAnnotationsBtn").style.visibility = "hidden";
    document.getElementById("returnBtn").style.visibility = "hidden";
    document.onkeyup = null;
    document.onkeydown = null;
    if (player.annotationPlayer) player.annotationPlayer.pause();
    document.body.style.background =
      "linear-gradient(to right, #1e425e, #839aa8, #1e425e)";
    const filePicker = document.getElementById("filePicker");
    filePicker.value = "";
    document.getElementById("playButton").onclick = () =>
      player.initializeOrSelectFiles();
    document.getElementById("files").textContent = "Select Files";
    player.annotationMode = false;
    document
      .getElementById("toggleAnnotationModeBtn")
      .classList.remove("active");
    if (player.annotationPlayer) player.annotationPlayer.resetAnnotations();
    player.annotations = null;
    player.currently = null;
    player.jsonFilePath = null;
    player.timeCache = 0;
  },

  reloadAnnotations: () => {
    if (!player.annotationPlayer.isPaused()) {
      player.annotationPlayer.pause();
    }

    console.log("Reloading Annotations");
    player.reloadingAnnotations = true;
    let reloadAnnotationsTime = player.annotationPlayer.getCurrentTime();
    player.paused = player.annotationPlayer.isPaused();

    if (player.annotationPlayer) player.annotationPlayer.resetAnnotations();

    var annotationData = fs.readFileSync(player.jsonFilePath);
    player.initializePlayerAndPlay(annotationData);
    player.timeCache = reloadAnnotationsTime;
    player.annotationPlayer.setCurrentTime(reloadAnnotationsTime);
    player.reloadingAnnotations = false;
  },

  getSelectedFiles: () => {
    const fileList = document.getElementById("filePicker").files;
    if (!fileList || fileList.length === 0) return null;

    let icfFile = null;
    // Find the .icf file in the selection
    for (let i = 0; i < fileList.length; i++) {
      if (fileList[i].name.toLowerCase().endsWith(".icf")) {
        icfFile = fileList[i];
        break;
      }
    }

    if (!icfFile) {
      console.warn("No .icf file selected.");
      return null;
    }

    const icfPath = webUtils.getPathForFile(icfFile);
    const icfData = fs.readFileSync(icfPath);
    const icfObj = JSON.parse(icfData);
    console.log("Parsed ICF data in getSelectedFiles:", icfObj);
    player.icfData = icfObj;

    const basePath = icfPath.replace(/\/[^/]*$/, "");

    const annotationPath = icfObj.annotation
      ? `${basePath}/${icfObj.annotation}`
      : null;
    const videoPath = `${basePath}/.ic/${icfObj.video}`;

    const annotationFile = annotationPath ? { path: annotationPath, name: icfObj.annotation } : null;
    const videoFile = { path: videoPath, name: icfObj.video };

    let subtitleTracks = null;
    if (icfObj.subtitle) {
      const processSubtitle = sub => {
        if (sub && sub.url) {
          const subtitlePath = `${basePath}/${sub.url}`;
          return { ...sub, url: subtitlePath };
        }
        return sub;
      };

      if (Array.isArray(icfObj.subtitle)) {
        subtitleTracks = icfObj.subtitle.map(processSubtitle);
      } else {
        const processed = processSubtitle(icfObj.subtitle);
        if (processed) {
          subtitleTracks = [processed];
        }
      }
    }

    return { annotationFile, icfFile, videoFile, subtitleTracks };
  },

  generateICDirectory: () => {
    var HOME = process.env.HOME;
    var videoFile = document.getElementById("mp4FilePicker").files[0];
    var videoFilePath = webUtils.getPathForFile(videoFile);
    if (videoFilePath === undefined) {
      videoFilePath = "";
    }
    var annotationFile = document.getElementById("jsonFilePicker").files[0];
    var annotationFilePath = webUtils.getPathForFile(annotationFile);
    if (annotationFilePath === undefined) {
      annotationFilePath = "";
    }
    var stem = videoFile.name.split(`.`)[0];
    var dirName = HOME + `/Desktop/` + stem;
    var hiddenDirName = dirName + `/.ic`;
    if (!fs.existsSync(hiddenDirName)) {
      fs.mkdirSync(hiddenDirName, { recursive: true });
    }
    fs.copyFile(videoFilePath, hiddenDirName + `/` + videoFile.name, (err) => {
      if (err) alert(err);
    });

    let annotationRelativePath;
    if (annotationFile) {
      fs.copyFile(annotationFilePath, dirName + `/` + stem + `.json`, (err) => {
        if (err) alert(err);
      });
      annotationRelativePath = stem + ".json";
    } else {
      annotationRelativePath = null;
    }

    var icfString = JSON.stringify({
      subtitle: null,
      video: videoFile.name,
      annotation: annotationRelativePath,
    });
    fs.writeFile(dirName + `/` + stem + `.icf`, icfString, `utf8`, (err) => {
      if (err) alert(err);
    });
    alert(
      `Unless you received errors, your IC file has been created on the Desktop.`,
    );
  },

  parseAndPlay: (annotationFile, initializePlayerAndPlay) => {
    player.jsonFilePath = annotationFile.path;
    fs.readFile(annotationFile.path, (err, annotationData) => {
      if (err) {
        return err;
      }
      initializePlayerAndPlay(annotationData);
    });
  },

  initializePlayerAndPlay: (annotationData) => {
    if (!player.annotationPlayer) {
      const options = {
        container: '.annotation-player-container',
        disabledControls: []
      };

      console.log("In initializePlayerAndPlay, player.icfData:", player.icfData);
      // If ICF data contains subtitle information, pass it as tracks
      if (player.selectedFiles && player.selectedFiles.subtitleTracks) {
        console.log("Found subtitle data:", player.selectedFiles.subtitleTracks);
        options.tracks = player.selectedFiles.subtitleTracks;
      }

      player.annotationPlayer = new AnnotationPlayer(options);
    }

    let parsedData = annotationData;
    if (
      annotationData &&
      (annotationData instanceof Buffer || typeof annotationData === "string")
    ) {
      try {
        parsedData = JSON.parse(annotationData.toString());
      } catch {
        parsedData = annotationData;
      }
    }
    player.annotationPlayer.loadData(parsedData);

    const files = player.selectedFiles; // Use cached files
    if (files && files["videoFile"] && files["videoFile"].path) {
      player.annotationPlayer.setVideoSource(files["videoFile"].path);
    }

    document.querySelector(".main-content").classList.add("active");
    document.querySelector(".annotation-player-container").style.display = "flex";
    document.getElementById("reloadAnnotationsBtn").style.visibility =
      player.annotationMode ? "visible" : "hidden";
    if (!window.screenTop && !window.screenY) {
      document.getElementById("returnBtn").style.visibility = "hidden";
    } else {
      document.getElementById("returnBtn").style.visibility = "visible";
    }
    document.getElementById("splashScreen").style.display = "none";
    document.body.style.background = "black";
    player.annotationPlayer.play();
  },
};

window.player = player;
