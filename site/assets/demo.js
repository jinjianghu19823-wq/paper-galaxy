(function () {
  const year = document.querySelector("[data-year]");
  if (year) {
    year.textContent = String(new Date().getFullYear());
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.querySelector(button.getAttribute("data-copy"));
      if (!target) {
        return;
      }
      const text = target.textContent.trim();
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = button.getAttribute("data-copied") || "Copied";
      } catch {
        button.textContent = button.getAttribute("data-failed") || "Copy failed";
      }
      window.setTimeout(() => {
        button.textContent = button.getAttribute("data-label") || "Copy";
      }, 1600);
    });
  });
})();
