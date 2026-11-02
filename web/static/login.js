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
});
systemDark.addEventListener("change", render);
render();
