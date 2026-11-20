document.addEventListener("click", (event) => {
  const toggle = event.target.closest(".guide-nav-toggle");
  if (!toggle) {
    return;
  }
  const expanded = toggle.getAttribute("aria-expanded") === "true";
  toggle.setAttribute("aria-expanded", String(!expanded));
  document.getElementById(toggle.getAttribute("aria-controls")).hidden = expanded;
});
