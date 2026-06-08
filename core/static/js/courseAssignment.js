import { getCSRFToken } from "./utils.js";

function getCollectionIdValue() {
  const collectionForm = document.getElementById("collection-settings-form");
  if (!collectionForm) {
    return;
  }
  const idInput = collectionForm.querySelector("input[name='id']");
  const idValue = idInput?.value;
  if (idValue === undefined) {
    console.error("Failed to get collection id from form");
    return;
  }
  return idValue;
}

function setupAssignCourseButton() {
  const semesterSelector = document.getElementById("semester-selector");
  const semester = semesterSelector.value;
  const yearSelector = document.getElementById("year-selector");
  const year = yearSelector.value;
  if (semester === undefined || year === undefined) {
    console.error("Failed to assign course to collection because semester and or year are undefined");
    return;
  }

  const departmentInput = document.getElementById("department");
  const catalogNumInput = document.getElementById("catalog-number");
  // get all section numbers from string. The must not start with 0 and be 1 - 3
  // total number characters
  const sectionRegex = /[1-9]{1}[0-9]{0,2}/g;
  const sectionsInput = document.getElementById("sections");

  const assignCourseButton = document.getElementById("assign-course-button");
  assignCourseButton.addEventListener("click", async () => {
    const assignmentResponse = await fetch("/collections/assign-course/", {
      method: "POST",
      headers: {
        "X-CSRFToken": getCSRFToken(),
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        "dept": departmentInput.value.toUpperCase(),
        "catalog_number": catalogNumInput.value,
        "sections": sectionsInput.value.match(sectionRegex),
        "semester": semester,
        "year": year,
        "collection_id": getCollectionIdValue()
      })
    });

    if (!assignmentResponse.ok) {
      console.error("Failed to assign course to collection");
      return;
    }

    const newHTML = await assignmentResponse.text();
    const courseAssignmentContainer = document.getElementById("course-assignment-container");
    courseAssignmentContainer.innerHTML = newHTML;
    setupAssignCourseButton();
  });
}

function initialize() {
  setupAssignCourseButton();
}

initialize();
