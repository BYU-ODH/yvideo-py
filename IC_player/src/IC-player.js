const fs = window.require("fs");
const { webUtils } = window.require("electron");
import { AnnotationPlayer } from "./AnnotationPlayer.js";

export const player = {
  annotationPlayer: null,
  annotationMode: false,

  initializeOrSelectFiles: () => {
    const files = player.getSelectedFiles();
    if (!files) {
      document.getElementById("filePicker").click();

      function onFileChange() {
        document.getElementById("files").textContent =
          player.getSelectedFiles().videoFile.name;
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
    player.parseAndPlay(files["jsonFile"], player.initializePlayerAndPlay);
  },

  hidePlayer: () => {
    document.getElementById("splashScreen").style.visibility = "visible";
    document.getElementById("player-container").style.visibility = "hidden";
    document.getElementById("playButton").classList.remove("ready");
    document.getElementById("reloadJsonBtn").style.visibility = "hidden";
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

  reloadJson: () => {
    if (!player.paused) {
      player.annotationPlayer.pause();
    }

    console.log("Reloading JSON");
    player.reloadingJson = true;
    let reloadJsonTime = player.annotationPlayer.videoElem.currentTime;
    player.paused = player.annotationPlayer.videoElem.paused;

    if (player.annotationPlayer) player.annotationPlayer.resetAnnotations();

    var fileData = fs.readFileSync(player.jsonFilePath);
    player.initializePlayerAndPlay(fileData);
    player.timeCache = reloadJsonTime;
    player.annotationPlayer.videoElem.currentTime = reloadJsonTime;
    player.reloadingJson = false;
  },

  getSelectedFiles: () => {
    var fileList = document.getElementById("filePicker").files,
      jsonFile = null,
      icfFile = null,
      videoFile = null,
      jsonFileExists = false,
      icfFileExists = false,
      videoFileExists = false;

    if (!fileList || fileList.length === 0) return null;

    for (var i = 0; i < fileList.length; i++) {
      var ext = fileList[i]["name"].split(".")[1];
      if (ext === "json") {
        jsonFileExists = true;
        jsonFile = fileList[i];
      } else if (ext === "icf") {
        icfFileExists = true;
        icfFile = fileList[i];
      } else if (ext === "mp4" || ext === "m4v") {
        videoFileExists = true;
        videoFile = fileList[i];
      } else {
        console.warn(`Unsupported file type: ${fileList[i]["name"]}`);
      }
    }

    // If the icf file is the only one selected, derive paths for json and video
    if (icfFileExists && (!jsonFileExists || !videoFileExists)) {
      const icfData = fs.readFileSync(webUtils.getPathForFile(icfFile));
      const icfObj = JSON.parse(icfData);

      const jsonPath = webUtils
        .getPathForFile(icfFile)
        .replace(/\/[^/]*$/, "/" + icfObj["annotation"]);
      const videoPath = webUtils
        .getPathForFile(icfFile)
        .replace(/\/[^/]*$/, "/.ic/" + icfObj["video"]);

      jsonFileExists = true;
      jsonFile = {
        path: jsonPath,
      };
      videoFileExists = true;
      videoFile = {
        path: videoPath,
        name: icfObj["video"],
      };
    }

    return jsonFile && videoFile
      ? { jsonFile: jsonFile, icfFile: icfFile, videoFile: videoFile }
      : false;
  },

  generateICDirectory: () => {
    var HOME = process.env.HOME;
    var videoFile = document.getElementById("mp4FilePicker").files[0];
    var videoFilePath = webUtils.getPathForFile(videoFile);
    if (videoFilePath === undefined) {
      videoFilePath = "";
    }
    var jsonFile = document.getElementById("jsonFilePicker").files[0];
    var jsonFilePath = webUtils.getPathForFile(jsonFile);
    if (jsonFilePath === undefined) {
      jsonFilePath = "";
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

    let jsonPath;
    if (jsonFile) {
      fs.copyFile(jsonFilePath, dirName + `/` + stem + `.json`, (err) => {
        if (err) alert(err);
      });
      jsonPath = stem + ".json";
    } else {
      jsonPath = null;
    }

    var icfString = JSON.stringify({
      subtitle: null,
      video: videoFile.name,
      annotation: jsonPath,
    });
    fs.writeFile(dirName + `/` + stem + `.icf`, icfString, `utf8`, (err) => {
      if (err) alert(err);
    });
    alert(
      `Unless you received errors, your IC file has been created on the Desktop.`,
    );
  },

  parseAndPlay: (jsonFile, initializePlayerAndPlay) => {
    player.jsonFilePath = jsonFile.path;
    fs.readFile(jsonFile.path, (err, fileData) => {
      if (err) {
        return err;
      }
      initializePlayerAndPlay(fileData);
    });
  },

  initializePlayerAndPlay: (fileData) => {
    if (!player.annotationPlayer) {
      player.annotationPlayer = new AnnotationPlayer({
        container: '#player-container',
        disabledControls: ['transcriptBtn'] // Hide transcript button for IC player
      });
    }

    let parsedData = fileData;
    if (
      fileData &&
      (fileData instanceof Buffer || typeof fileData === "string")
    ) {
      try {
        parsedData = JSON.parse(fileData.toString());
      } catch {
        parsedData = fileData;
      }
    }
    player.annotationPlayer.loadData(parsedData);

    const files = player.getSelectedFiles();
    if (files && files["videoFile"] && files["videoFile"].path) {
      player.annotationPlayer.videoElem.src = files["videoFile"].path;
    }

    document.getElementById("player-container").style.visibility = "visible";
    document.getElementById("reloadJsonBtn").style.visibility =
      player.annotationMode ? "visible" : "hidden";
    if (!window.screenTop && !window.screenY) {
      document.getElementById("returnBtn").style.visibility = "hidden";
    } else {
      document.getElementById("returnBtn").style.visibility = "visible";
    }
    document.getElementById("splashScreen").style.visibility = "hidden";
    document.body.style.background = "black";
    player.annotationPlayer.play();
  },
};

window.player = player;
