const fs = window.require("fs");
const { webUtils } = window.require("electron");
import { AnnotationPlayer } from "./annotation-player.js";

export const player = {
  annotationPlayer: null,
  annotationMode: false, // Track annotation mode state

  initializeOrSelectFiles: () => {
    const files = player.getSelectedFiles();
    if (!files) {
      document.getElementById("filePicker").click();
      // Use a named function so we can remove it as an event listener
      function onFileChange() {
        document.getElementById("files").textContent =
          player.getSelectedFiles().videoFile.name;
        document.getElementById("playButton").classList.add("ready");
        document
          .getElementById("filePicker")
          .removeEventListener("change", onFileChange);
        // Add play button event listener here
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
    // Toggle the annotation mode state
    player.annotationMode = !player.annotationMode;

    // Update button appearance to match state
    const toggleBtn = document.getElementById("toggleAnnotationModeBtn");
    if (player.annotationMode) {
      toggleBtn.classList.add("active");
    } else {
      toggleBtn.classList.remove("active");
    }

    // Toggle dev tools if available
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
    // Show Splash Screen
    document.getElementById("splashScreen").style.visibility = "visible";

    // Hide Player
    document.getElementById("playerContainer").style.visibility = "hidden";

    // Remove 'ready' class from playButton
    document.getElementById("playButton").classList.remove("ready");

    // Hide Reload JSON button if visible
    document.getElementById("reloadJsonBtn").style.visibility = "hidden";
    document.getElementById("returnBtn").style.visibility = "hidden";

    // Remove keyup listener
    document.onkeyup = null;
    document.onkeydown = null;

    // Pause the video
    if (player.annotationPlayer) player.annotationPlayer.pause();

    // Set background to normal
    document.body.style.background =
      "linear-gradient(to right, #1e425e, #839aa8, #1e425e)";

    // Completely reset file picker
    const filePicker = document.getElementById("filePicker");
    filePicker.value = "";
    // Reset the play button handler back to initializeOrSelectFiles
    document.getElementById("playButton").onclick = () =>
      player.initializeOrSelectFiles();
    document.getElementById("files").textContent = "Select Files";

    // Reset annotation mode state
    player.annotationMode = false;
    document
      .getElementById("toggleAnnotationModeBtn")
      .classList.remove("active");

    // Reset player variables
    if (player.annotationPlayer) player.annotationPlayer.resetAnnotations();
    player.annotations = null;
    player.currently = null;
    player.jsonFilePath = null;
    player.timeCache = 0;
  },

  // Reload the json annotations and begin player with same settings
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

  // Load the files
  getSelectedFiles: () => {
    var fileList = document.getElementById("filePicker").files,
      jsonFile = null,
      icfFile = null,
      videoFile = null,
      jsonFileExists = false,
      icfFileExists = false,
      videoFileExists = false;

    // More defensive check - ensure filePicker has files and length > 0
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

    // If the icf file is the only one selected
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

    // if all the necessary files are included, return the file mapping; else return false
    return jsonFile && videoFile
      ? { jsonFile: jsonFile, icfFile: icfFile, videoFile: videoFile }
      : false;
  },

  // Create IC directory from mp4 (and optionally an annotations JSON)
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

  // Parse jsonFile and initialize player
  parseAndPlay: (jsonFile, initializePlayerAndPlay) => {
    player.jsonFilePath = jsonFile.path;
    fs.readFile(jsonFile.path, (err, fileData) => {
      if (err) {
        return err;
      }
      initializePlayerAndPlay(fileData);
    });
  },

  // Callback function when loading any data
  initializePlayerAndPlay: (fileData) => {
    // Setup AnnotationPlayer instance
    const videoElem = document.getElementById("player");
    const annotationContainer = document.getElementById("annotation-container");
    if (!player.annotationPlayer) {
      player.annotationPlayer = new AnnotationPlayer(
        videoElem,
        annotationContainer,
      );
    }
    // Defensive: parse fileData if it's a Buffer or string
    let parsedData = fileData;
    if (
      fileData &&
      (fileData instanceof Buffer || typeof fileData === "string")
    ) {
      try {
        parsedData = JSON.parse(fileData.toString());
      } catch (e) {
        parsedData = fileData;
      }
    }
    player.annotationPlayer.loadAnnotations(parsedData);

    // --- UI logic to show/hide splash and player ---
    // Set video src to given file
    const files = player.getSelectedFiles();
    if (files && files["videoFile"] && files["videoFile"].path) {
      videoElem.src = files["videoFile"].path;
    }

    // Show Player
    document.getElementById("playerContainer").style.visibility = "visible";

    // Show Reload JSON button if annotationMode = true
    document.getElementById("reloadJsonBtn").style.visibility =
      player.annotationMode ? "visible" : "hidden";

    // Show/hide return button depending on window state
    if (!window.screenTop && !window.screenY) {
      document.getElementById("returnBtn").style.visibility = "hidden";
    } else {
      document.getElementById("returnBtn").style.visibility = "visible";
    }

    // Hide Splash Screen
    document.getElementById("splashScreen").style.visibility = "hidden";

    // Set background to black
    document.body.style.background = "black";

    // Play the video
    player.annotationPlayer.play();
  },
};

window.player = player;
