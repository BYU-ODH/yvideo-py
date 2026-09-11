const legacyMigrationForm = document.querySelector(".legacy-migrations-form");

if (legacyMigrationForm) {
    const complianceCheckboxes = [
        legacyMigrationForm.elements.acknowledged_compliance,
        legacyMigrationForm.elements.acknowledged_fair_use_limitation,
    ];
    const submitButton = legacyMigrationForm.querySelector(
        ".legacy-migrations-submit",
    );
    const updateSubmitButton = () => {
        submitButton.disabled = !complianceCheckboxes.every(
            (checkbox) => checkbox.checked,
        );
    };

    complianceCheckboxes.forEach((checkbox) => {
        checkbox.addEventListener("change", updateSubmitButton);
    });
    updateSubmitButton();
}
