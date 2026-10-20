const HEX_COLOR = /^(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;

export function isHexColor(value) {
  return HEX_COLOR.test(value);
}

export function setUpColorCombobox(input) {
  const combobox = input.closest(".color-combobox");
  const swatch = combobox.querySelector(":scope > .color-swatch");
  const toggle = combobox.querySelector(".color-combobox-toggle");
  const listbox = combobox.querySelector("[role=listbox]");
  const options = Array.from(listbox.querySelectorAll("[role=option]"));
  let activeIndex = -1;

  const isOpen = () => !listbox.hidden;

  const setActive = (index) => {
    options[activeIndex]?.classList.remove("active");
    activeIndex = index;
    const option = options[activeIndex];
    if (option) {
      option.classList.add("active");
      option.scrollIntoView({block: "nearest"});
      input.setAttribute("aria-activedescendant", option.id);
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  };

  const syncSelection = () => {
    const value = input.value.toLowerCase();
    for (const option of options) {
      option.setAttribute("aria-selected", String(option.dataset["value"] === value));
    }
    if (isHexColor(value)) {
      swatch.style.backgroundColor = "#" + value;
    }
  };

  const open = () => {
    if (input.readOnly || isOpen()) return;
    listbox.hidden = false;
    input.setAttribute("aria-expanded", "true");
    setActive(options.findIndex((option) => option.getAttribute("aria-selected") === "true"));
  };

  const close = () => {
    if (!isOpen()) return;
    listbox.hidden = true;
    input.setAttribute("aria-expanded", "false");
    setActive(-1);
  };

  const choose = (option) => {
    input.value = option.dataset["value"];
    close();
    input.dispatchEvent(new Event("input", {bubbles: true}));
    input.dispatchEvent(new Event("change", {bubbles: true}));
  };

  input.addEventListener("input", () => {
    // Users often paste CSS-style codes like "#ff0000"; the server stores bare hex.
    if (input.value.startsWith("#")) {
      input.value = input.value.slice(1);
    }
    syncSelection();
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!isOpen()) {
        open();
        return;
      }
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((activeIndex + step + options.length) % options.length);
    } else if (e.key === "Enter" && isOpen() && activeIndex >= 0) {
      e.preventDefault();
      choose(options[activeIndex]);
    } else if (e.key === "Escape" && isOpen()) {
      e.preventDefault();
      e.stopPropagation();
      close();
    }
  });

  input.addEventListener("blur", close);

  toggle.addEventListener("click", () => {
    if (isOpen()) {
      close();
    } else {
      input.focus();
      open();
    }
  });

  // Keep focus in the input so its blur handler does not close the list before the click lands.
  listbox.addEventListener("mousedown", (e) => e.preventDefault());
  toggle.addEventListener("mousedown", (e) => e.preventDefault());

  listbox.addEventListener("click", (e) => {
    const option = e.target.closest("[role=option]");
    if (option) choose(option);
  });

  syncSelection();
}
