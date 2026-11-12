// Drag-and-drop reordering of a program's items. The ↑/↓ buttons do the same without a
// mouse, so this is a convenience, not the only way.

import { getCSRFToken } from "../../js/utils.js";

const list = document.getElementById("ic-items");

if (list) {
  let dragged = null;

  list.addEventListener("dragstart", (event) => {
    dragged = event.target.closest(".ic-item");
    if (dragged) event.dataTransfer.effectAllowed = "move";
  });

  list.addEventListener("dragover", (event) => {
    const over = event.target.closest(".ic-item");
    if (!dragged || !over || over === dragged) return;
    event.preventDefault();
    const box = over.getBoundingClientRect();
    const after = event.clientY > box.top + box.height / 2;
    over.parentNode.insertBefore(dragged, after ? over.nextSibling : over);
  });

  list.addEventListener("dragend", async () => {
    if (!dragged) return;
    dragged = null;
    const itemIds = Array.from(list.querySelectorAll(".ic-item"), (li) => Number(li.dataset.itemId));
    await fetch(list.dataset.reorderUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
      body: JSON.stringify({ item_ids: itemIds }),
    });
    // Reload so the numbering and readiness reflect the saved order (or the old one on failure).
    window.location.reload();
  });
}
