document.addEventListener('DOMContentLoaded', function () {
    const spoofInput = document.getElementById('spoof-user-input');
    const spoofSelect = document.getElementById('spoof-user-select');
    const spoofSubmit = document.getElementById('spoof-user-submit');
    if (!spoofSelect || !spoofInput || !spoofSubmit) {
        return;
    }

    function hasSelectableOptions() {
        const options = spoofSelect.options;
        return options.length > 0 && !(options.length === 1 && options[0].disabled);
    }

    function applySelectedOption(option) {
        if (option && option.value && !option.disabled) {
            spoofInput.value = option.text;
            spoofSubmit.disabled = false;
        } else {
            spoofSubmit.disabled = true;
        }
    }

    function closeDropdown() {
        spoofSelect.classList.remove('show');
    }

    spoofSelect.addEventListener('change', function () {
        applySelectedOption(spoofSelect.options[spoofSelect.selectedIndex]);
    });
    spoofInput.addEventListener('input', function () {
        spoofSubmit.disabled = true;
        // Show select if there's a query and options exist
        if (spoofInput.value && spoofSelect.options.length > 0) {
            spoofSelect.classList.add('show');
        } else {
            closeDropdown();
        }
    });
    // The native "x" clear button on a type=search input fires a `search`
    // event once it empties the field; treat that as closing the dropdown.
    spoofInput.addEventListener('search', function () {
        if (!spoofInput.value) {
            closeDropdown();
        }
    });
    // Arrow keys move the highlighted option without leaving the search
    // input; Enter submits whatever is currently highlighted.
    spoofInput.addEventListener('keydown', function (event) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            if (!hasSelectableOptions()) {
                return;
            }
            event.preventDefault();
            const options = spoofSelect.options;
            const direction = event.key === 'ArrowDown' ? 1 : -1;
            let index = spoofSelect.selectedIndex;
            if (index < 0) {
                index = direction === 1 ? 0 : options.length - 1;
            } else {
                index = Math.min(Math.max(index + direction, 0), options.length - 1);
            }
            spoofSelect.selectedIndex = index;
            applySelectedOption(options[index]);
            options[index].scrollIntoView({ block: 'nearest' });
        } else if (event.key === 'Enter') {
            if (!spoofSubmit.disabled && spoofSelect.value) {
                event.preventDefault();
                spoofSubmit.click();
            }
        }
    });
    // When HTMX updates select, show if there's still a query and options exist
    document.body.addEventListener('htmx:afterSwap', function (evt) {
        if (evt.target.id === 'spoof-user-select') {
            if (spoofInput.value && spoofSelect.options.length > 0) {
                spoofSelect.classList.add('show');
            } else {
                closeDropdown();
            }
        }
    });
});
