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

function getYear() {
  const yearSelector = document.getElementById("year-selector");
  return yearSelector.value;
}

function getSemester() {
  const semesterSelector = document.getElementById("semester-selector");
  return semesterSelector.value;
}

function cleanSectionsInput(sectionsStr) {
  const sectionRegex = /[1-9]{1}[0-9]{0,2}/g;
  return sectionsStr.match(sectionRegex);
}

function setupAssignCourseButton() {
  const semester = getSemester();
  const year = getYear();
  if (semester === undefined || year === undefined) {
    console.error("Failed to assign course to collection because semester and or year are undefined");
    return;
  }

  const departmentInput = document.getElementById("department");
  const catalogNumInput = document.getElementById("catalog-number");
  // get all section numbers from string. The must not start with 0 and be 1 - 3
  // total number characters
  const sectionsInput = document.getElementById("sections");

  const assignCourseButton = document.getElementById("assign-course-button");
  assignCourseButton.addEventListener("click", async () => {
    const dept = departmentInput.value.toUpperCase();
    let invalid = false;
    if (dept == "") {
      departmentInput.classList.add("invalid-input");
      invalid = true;
    }
    const catalogNumber = catalogNumInput.value.toUpperCase();
    if (catalogNumber == "") {
      catalogNumInput.classList.add("invalid-input");
      invalid = true;
    }
    const sections = cleanSectionsInput(sectionsInput.value);
    if (!sections) {
      sectionsInput.classList.add("invalid-input");
      invalid = true;
    }
    if (invalid) {
      return;
    }
    const assignmentResponse = await fetch("/collections/assign-course/", {
      method: "POST",
      headers: {
        "X-CSRFToken": getCSRFToken(),
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        "dept": dept,
        "catalog_number": catalogNumber,
        "sections": sections,
        "semester": semester,
        "year": year,
        "collection_id": getCollectionIdValue()
      })
    });

    if (!assignmentResponse.ok) {
      console.error("Failed to assign course to collection");
      return;
    }

    departmentInput.classList.remove("invalid-input");
    catalogNumInput.classList.remove("invalid-input");
    sectionsInput.classList.remove("invalid-input");
    const newHTML = await assignmentResponse.text();
    const courseAssignmentContainer = document.getElementById("course-assignment-container");
    courseAssignmentContainer.innerHTML = newHTML;
    initialize();
  });
}

function setupSubmitSectionButtons() {
  const courseItems = document.getElementsByClassName("course-item");
  for (let item of courseItems) {
    const sectionInput = item.querySelector(".section-input");
    let originalInput = sectionInput.value;
    const dept = item.dataset["dept"];
    const catalogNumber = item.dataset["catalogNumber"];
    const submitButton = item.querySelector(".section-save-button");
    sectionInput.addEventListener("input", (event) => {
      if (event.target.value != originalInput) {
        submitButton.classList.remove("hidden");
      } else {
        submitButton.classList.add("hidden");
      }
    });
    submitButton.addEventListener("click", async () => {
      const semester = getSemester();
      const year = getYear();
      if (year === undefined || semester === undefined) {
        console.error("Failed to update sections because year and/or semseter are undefined");
        return;
      }
      const saveResponse = await fetch("/collections/course/update-sections/", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          "collection_id": getCollectionIdValue(),
          "sections": cleanSectionsInput(sectionInput.value),
          "dept": dept,
          "catalog_number": catalogNumber,
          "semester": semester,
          "year": year
        })
      });

      if (!saveResponse.ok) {
        console.error("Failed to update sections because of a bad request");
        return;
      }

      originalInput = sectionInput.value;
      submitButton.classList.add("hidden");
    });
  }
}

function setupRemoveCourseButtons() {
  const assignedCourseEls = document.getElementsByClassName("assigned-course");
  for (let assignedCourseEl of assignedCourseEls) {
    const dept = assignedCourseEl.dataset["dept"];
    const catalogNumber = assignedCourseEl.dataset["catalogNumber"];
    const semester = getSemester();
    const year = getYear();
    const collectionId = getCollectionIdValue();

    assignedCourseEl.addEventListener("click", async (event) => {
      if (!event.target.closest(".remove-course-assignment-button")) {
        return;
      }
      const removeRequest = await fetch("/collections/course/unassign/", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          dept: dept,
          catalog_number: catalogNumber,
          semester: semester,
          year: year,
          collection_id: collectionId
        })
      });

      if (!removeRequest.ok) {
        console.error("Failed to remove collection from course");
        return;
      }

      assignedCourseEl.remove();
    });
  }
}

function initialize() {
  setupAssignCourseButton();
  setupSubmitSectionButtons();
  setupRemoveCourseButtons();
}

initialize();
