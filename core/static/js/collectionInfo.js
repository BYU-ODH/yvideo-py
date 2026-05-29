// import { getCSRFToken } from "./utils.js";

function setupVideoSearch() {
  const searchInput = document.getElementById("video-search");
  const parent = searchInput.closest("#collection-videos-content");
  const videoList = parent.querySelector("#collection-video-list");
  searchInput.addEventListener("input", () => {
    const searchText = searchInput.value.toLowerCase();
    const videos = videoList.querySelectorAll(".collection-video");
    for (const video of videos) {
      const title = video.querySelector(".video-title")?.innerText.toLowerCase() || "";
      video.style.display = title.includes(searchText) ? "" : "none";
    }
  });
}

function initialize() {
  setupVideoSearch();
}

initialize();
