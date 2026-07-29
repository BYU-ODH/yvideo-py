document.addEventListener("DOMContentLoaded", function () {
  var form = document.getElementById("changelist-form");
  if (!form) {
    return;
  }
  form.addEventListener("submit", function (event) {
    var actionSelect = form.querySelector('select[name="action"]');
    if (!actionSelect || actionSelect.value !== "run_preflight_action") {
      return;
    }
    var confirmed = confirm(
      "Running preflight again will erase any file decisions, user " +
        "mappings, and issues already recorded for the selected request(s), " +
        "and rebuild them from the legacy data. This cannot be undone. " +
        "Continue?"
    );
    if (!confirmed) {
      event.preventDefault();
    }
  });
});
