document.addEventListener("DOMContentLoaded", function () {
  var checkedOutFromHbll = document.querySelector("#id_checked_out_from_hbll");
  var checkedOutFromOtherByu = document.querySelector(
    "#id_checked_out_from_other_byu_library"
  );
  var callNumberRow = document.querySelector(".form-row.field-byu_call_number");

  if (!callNumberRow) {
    return;
  }

  function syncCallNumberRow() {
    var isCheckedOut =
      (checkedOutFromHbll && checkedOutFromHbll.checked) ||
      (checkedOutFromOtherByu && checkedOutFromOtherByu.checked);
    callNumberRow.classList.toggle("hidden", !isCheckedOut);
  }

  if (checkedOutFromHbll) {
    checkedOutFromHbll.addEventListener("change", syncCallNumberRow);
  }
  if (checkedOutFromOtherByu) {
    checkedOutFromOtherByu.addEventListener("change", syncCallNumberRow);
  }
  syncCallNumberRow();
});
