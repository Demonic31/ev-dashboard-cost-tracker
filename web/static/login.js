// Bouton de thème de la mire : même mémorisation que le dashboard (localStorage "theme")
const systemDark = matchMedia("(prefers-color-scheme: dark)");
const isDark = () => document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : systemDark.matches;
const button = document.getElementById("theme");
function render() {
  const dark = isDark();
  button.textContent = dark ? "☀️ Thème clair" : "🌙 Thème sombre";
}
button.addEventListener("click", () => {
  const theme = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch (e) {}
  render();
  applyThemeColor();
});
systemDark.addEventListener("change", render);
render();

// Lancée comme application (PWA) : on garde la connexion par défaut, sinon Android la perd à chaque fermeture
if (matchMedia("(display-mode: standalone)").matches) document.querySelector("input[name=remember]").checked = true;
