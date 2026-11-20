const form = document.getElementById("resource-access-form");
if (form) {
  const checkboxes = [...form.querySelectorAll('input[type="checkbox"]')];
  const updateButtons = () => {
    const agreed = checkboxes.every((checkbox) => checkbox.checked);
    for (const button of form.querySelectorAll("button[formaction]")) {
      button.disabled = !agreed;
    }
  };
  form.addEventListener("change", updateButtons);
  updateButtons();
}
