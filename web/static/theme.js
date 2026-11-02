// Thème choisi avec le bouton (mémorisé), appliqué avant le rendu pour éviter un flash
try { const t = localStorage.getItem("theme"); if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; } catch (e) {}
