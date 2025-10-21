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
