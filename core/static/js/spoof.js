document.addEventListener('DOMContentLoaded', function () {
    const spoofInput = document.getElementById('spoof-user-input');
    const spoofSelect = document.getElementById('spoof-user-select');
    const spoofSubmit = document.getElementById('spoof-user-submit');
    if (!spoofSelect || !spoofInput || !spoofSubmit) {
        return;
    }

    spoofSelect.addEventListener('change', function () {
        const selectedOption = spoofSelect.options[spoofSelect.selectedIndex];
        if (selectedOption && selectedOption.value) {
            spoofInput.value = selectedOption.text;
            spoofSubmit.disabled = false;
        } else {
            spoofSubmit.disabled = true;
        }
    });
    spoofInput.addEventListener('input', function () {
        spoofSubmit.disabled = true;
        // Show select if options exist
        if (spoofSelect.options.length > 0) {
            spoofSelect.classList.add('show');
        } else {
            spoofSelect.classList.remove('show');
        }
    });
    // Hide select when input loses focus and select not hovered
    spoofInput.addEventListener('blur', function () {
        setTimeout(() => {
            spoofSelect.classList.remove('show');
        }, 200);
    });
    spoofSelect.addEventListener('mouseenter', function () {
        spoofSelect.classList.add('show');
    });
    spoofSelect.addEventListener('mouseleave', function () {
        spoofSelect.classList.remove('show');
    });
    // When HTMX updates select, show if options exist
    document.body.addEventListener('htmx:afterSwap', function (evt) {
        if (evt.target.id === 'spoof-user-select') {
            if (spoofSelect.options.length > 0) {
                spoofSelect.classList.add('show');
            } else {
                spoofSelect.classList.remove('show');
            }
        }
    });
});
