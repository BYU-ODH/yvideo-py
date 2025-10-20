/* eslint no-unused-vars: off */
"use strict";

function emptyParentInnerHTML(element, cssSelectorForParent) {
  const parent = element.closest(cssSelectorForParent);
  if (parent) {
    parent.innerHTML = "";
  } else {
    console.log(
      "element with selector: " + cssSelectorForParent + " not found!",
    );
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
