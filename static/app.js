(() => {
  "use strict";

  const root = document.documentElement;
  const themeButtons = document.querySelectorAll("[data-theme-toggle]");

  const updateThemeButtons = () => {
    const isLight = root.dataset.theme === "light";
    themeButtons.forEach((button) => {
      button.setAttribute("aria-pressed", String(isLight));
      button.setAttribute("aria-label", isLight ? "切换到深色主题" : "切换到浅色主题");
      const symbol = button.querySelector(".theme-symbol");
      if (symbol) symbol.textContent = isLight ? "☀" : "☾";
    });
  };

  themeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const nextTheme = root.dataset.theme === "light" ? "dark" : "light";
      root.dataset.theme = nextTheme;
      try {
        localStorage.setItem("ashare-theme", nextTheme);
      } catch (_) {
        // Private browsing may reject localStorage; the current page still updates.
      }
      updateThemeButtons();
    });
  });
  updateThemeButtons();

  // Keep the active navigation item visible after horizontal scrolling on small screens.
  const activeLink = document.querySelector(".nav-link.is-active");
  if (activeLink && window.matchMedia("(max-width: 900px)").matches) {
    window.requestAnimationFrame(() => activeLink.scrollIntoView({ inline: "center", block: "nearest" }));
  }

  document.querySelectorAll("[data-refresh]").forEach((button) => {
    button.addEventListener("click", () => {
      button.setAttribute("aria-busy", "true");
      button.classList.add("is-loading");
      window.location.reload();
    });
  });
})();
