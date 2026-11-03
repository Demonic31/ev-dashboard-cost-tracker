// Thème choisi avec le bouton (mémorisé), appliqué avant le rendu pour éviter un flash
try { const t = localStorage.getItem("theme"); if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; } catch (e) {}
// Couleur des barres Android (PWA) : suit le thème choisi au bouton, sinon celui du téléphone
const THEME_COLORS = { light: "#f4f6f9", dark: "#0f141b" };
function applyThemeColor() {
  const t = document.documentElement.dataset.theme;
  document.querySelectorAll('meta[name="theme-color"]').forEach(m => {
    m.content = THEME_COLORS[t || (m.media.includes("dark") ? "dark" : "light")];
  });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", applyThemeColor); else applyThemeColor();
