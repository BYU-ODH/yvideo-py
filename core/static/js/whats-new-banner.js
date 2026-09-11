// Bump the version suffix to announce something new to people who dismissed the last one.
const DISMISSED_KEY = "yvideo-whats-new-v1";

function setupWhatsNewBanner() {
  const banner = document.getElementById("whats-new-banner");
  if (!banner) {
    return;
  }
  // Private browsing and blocked storage throw on access rather than returning null.
  let dismissed;
  try {
    dismissed = localStorage.getItem(DISMISSED_KEY) !== null;
  } catch {
    dismissed = false;
  }
  if (dismissed) {
    return;
  }
  banner.hidden = false;

  document.getElementById("whats-new-dismiss")?.addEventListener("click", () => {
    banner.hidden = true;
    try {
      localStorage.setItem(DISMISSED_KEY, "1");
    } catch {
      // Dismissal not remembered; hiding it for this page view is still the right response.
    }
  });
}

setupWhatsNewBanner();
