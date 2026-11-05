// Captures du README (PC et mobile, thèmes clair et sombre) avec le jeu de données fictif de gen_demo.py.
// Lancé par run.sh dans l'image Docker de Playwright : BASE_URL = dashboard, DATA = jeu fictif, OUT = dossier de sortie.
const { chromium } = require("playwright");
const fs = require("fs");
const BASE = process.env.BASE_URL.replace(/\/?$/, "/");
const data = fs.readFileSync(process.env.DATA, "utf8");
const OUT = process.env.OUT;
(async () => {
  const b = await chromium.launch();
  let failed = false;
  for (const [dev, w, scale] of [["pc", 1280, 1], ["mobile", 390, 3]]) for (const scheme of ["light", "dark"]) {
    const p = await b.newPage({ viewport: { width: w, height: dev === "pc" ? 900 : 844 }, deviceScaleFactor: scale, colorScheme: scheme,
                                timezoneId: "Europe/Paris", locale: "fr-FR" });
    const errs = []; p.on("pageerror", e => errs.push(e.message)); p.on("console", m => m.type() === "error" && errs.push(m.text()));
    // heure du jeu fictif (5 nov. 2026, 23:05) ; l'horloge continue d'avancer pour les animations des graphiques
    await p.clock.setSystemTime(new Date("2026-11-05T22:05:00Z"));
    await p.route("**/api/data*", r => r.fulfill({ contentType: "application/json", body: data }));
    // logo personnel éventuel de l'instance (DASHBOARD_LOGO) remplacé par l'icône neutre du projet
    await p.route(u => u.href === BASE, async r => {
      const resp = await r.fetch(); const html = (await resp.text())
        .replace(/<img class="logo[^"]*" src="local\/logo" alt="">/, '<img class="logo" src="static/favicon.svg" alt="">');
      r.fulfill({ response: resp, body: html });
    });
    await p.goto(BASE); await p.waitForSelector("tr.month-row");
    await p.selectOption("#preset", "season"); await p.waitForTimeout(500);
    if (dev === "pc") {  // mois le plus récent des recharges déplié, et la courbe de sa première recharge
      await p.evaluate(() => { const r = document.querySelector("tr[data-id]");
        r.closest("table").querySelector(`tr.month-row[data-month="${r.dataset.in}"]`).click(); });
      await p.click("tr[data-id] >> nth=0");
      // page entière sans fullPage (qui redimensionne et relance l'animation des graphiques)
      await p.setViewportSize({ width: w, height: await p.evaluate(() => document.documentElement.scrollHeight) });
    }
    await p.waitForTimeout(2000);
    await p.screenshot({ path: `${OUT}/${dev}-${scheme}.png` });
    console.log(`${dev}-${scheme}.png`, errs.length ? errs : "ok");
    failed ||= errs.length > 0;
  }
  await b.close();
  process.exit(failed ? 1 : 0);
})();
