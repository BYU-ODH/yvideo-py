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
