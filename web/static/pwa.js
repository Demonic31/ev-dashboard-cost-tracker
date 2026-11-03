// Enregistre le service worker de la PWA (page hors connexion), sur la mire comme sur le dashboard
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
