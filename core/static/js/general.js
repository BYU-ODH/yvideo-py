/* eslint no-unused-vars: off */
"use strict";

function deleteParent(element, cssSelectorForParent) {
  const parent = element.closest(cssSelectorForParent);
  if (parent) {
    parent.remove();
  } else {
    console.log("element with selector: " + cssSelectorForParent + " not found!");
  }
}

function elevateFromSiblings(element) {
  const parent = element.parentElement;
  for (let sibling of parent.children) {
    if (sibling != element) {
      sibling.classList.remove("elevated");
    } else {
      sibling.classList.add("elevated");
    }
  }
}

function doesArrayContainString(array, string) {
  for (let value of array) {
    if (value == string) {
      return true;
    }
  }
  return false;
}

function handleAccordian(
  element,
  cssSelectorForAssociatedArrow = null,
  cssSelectorForAffectedElement = null,
) {
  // affected element will be set to next sibling if not specified
  let affectedElement;
  if (cssSelectorForAffectedElement == null) {
    affectedElement = element.nextElementSibling;
  } else {
    affectedElement = document.querySelector(cssSelectorForAffectedElement);
  }

  // will search for arrow in children if not specified
  let associatedArrow;
  if (cssSelectorForAssociatedArrow != null) {
    associatedArrow = document.querySelector(cssSelectorForAssociatedArrow);
  } else {
    for (let child of element.children) {
      if (doesArrayContainString(child.classList, "arrow")) {
        associatedArrow = child;
        break;
      }
    }
  }

  // the affected element should have the "accordian-folded" class initially
  if (affectedElement) {
    affectedElement.classList.toggle("accordian-folded");
  }
  if (associatedArrow) {
    associatedArrow.classList.toggle("turned");
  }
}

function closeUserViewModalFromBackdrop(event) {
  const dialog = event.target;
  if (!(dialog instanceof HTMLDialogElement) || !dialog.matches(".user-view-modal")) {
    return;
  }

  const bounds = dialog.getBoundingClientRect();
  const clickedOutside = event.clientX < bounds.left
    || event.clientX > bounds.right
    || event.clientY < bounds.top
    || event.clientY > bounds.bottom;
  if (clickedOutside) {
    dialog.close();
  }
}

document.addEventListener("click", closeUserViewModalFromBackdrop);

function openPreviousUserViewModal(event) {
  const backButton = event.target.closest("[data-previous-dialog-id]");
  if (!backButton) {
    return;
  }

  const currentDialog = backButton.closest("dialog");
  const previousDialog = document.getElementById(backButton.dataset.previousDialogId);
  if (!(currentDialog instanceof HTMLDialogElement)
    || !(previousDialog instanceof HTMLDialogElement)) {
    return;
  }

  currentDialog.close();
  previousDialog.showModal();
}

document.addEventListener("click", openPreviousUserViewModal);
