function setupReset() {
  const resetButton = document.getElementById("content-settings-reset-button");
  const contentIdInput = document.querySelector("input[name='id']");
  if (!resetButton || !contentIdInput) {
    console.error("resetButton or contentIdInput are not defined");
    return;
  }
  resetButton.addEventListener("click", async () => {
    const contentId = contentIdInput.value;
    const resetResponse = await fetch(`/content/render-settings-form/${contentId}/`);
    if (!resetResponse.ok) {
      console.error("Failed to reset content settings form");
      return;
    }

    const newFormHTML = await resetResponse.text();
    const oldForm = document.getElementById("content-settings-form");
    oldForm.outerHTML = newFormHTML;
  });
}

function initialize() {
  setupReset();
}

initialize();
