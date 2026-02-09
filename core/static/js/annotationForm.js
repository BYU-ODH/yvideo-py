async function deleteItem() {
  const itemForm = document.getElementById("existing-item-form");
  const itemType = itemForm.dataset["itemtype"];
  const annotationId = itemForm.dataset["annotationid"];
  const item = document.querySelector(`.layer-item[data-item-id=${annotationId}]`);
  const response = await fetch(`annotations/${itemType}/${annotationId}/delete`, {
    method: "delete"
  });
  if (response.status == 200) {
    item.remove();
  }
}

function init() {
  const deleteButton = document.getElementById("annotation-form-delete-button");
  deleteButton.addEventListener("click", deleteItem);
}

init();
