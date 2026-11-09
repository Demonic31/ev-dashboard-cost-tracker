const fr = (n, d = 1) => (n === "" || n === null || n === undefined || isNaN(n)) ? "–"
  : Number(n).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
const eur = (n, d = 2) => fr(n, d) === "–" ? "–" : fr(n, d) + " €";
const dt = s => s ? new Date(s).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", year: "2-digit",
  hour: "2-digit", minute: "2-digit" }) : "–";
const dshort = s => new Date(s + "T12:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = v => typeof v === "number" ? v : 0;
const sum = (rows, i) => rows.reduce((s, r) => s + num(r[i]), 0);
const ratio = (a, b, k = 1) => b ? a / b * k : "";
const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const store = { get: k => { try { return localStorage.getItem(k); } catch { return null; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* stockage indisponible */ } } };

// Colonnes (0-based) de l'onglet Recharges A:W et Trajets A:H
const C = { start: 1, kwhBat: 5, slots: 7, note: 14, duration: 15, color: 16, kwh: 17, cost: 18, eurKwh: 19, km: 20, eur100: 21 };
const T = { id: 0, start: 1, end: 2, km: 3, kwh: 4, kwh100: 5, odo: 6 };
// Format des KPI du Dashboard, dans l'ordre des colonnes A:I du Sheet
const KPI_FMT = [v => eur(v), v => fr(v, 1), v => fr(v, 0), v => eur(v), v => fr(v, 1), v => eur(v, 4),
                 v => fr(v, 0), v => fr(v, 0), v => fr(v, 1)];
const TRANCHES = ["Bleu HC", "Bleu HP", "Blanc HC", "Blanc HP", "Rouge HC", "Rouge HP"];
// Une teinte par couleur de jour ; HC en aplat, HP en hachures (tranches d'indice impair)
const TRANCHE_COLORS = ["--bleu", "--bleu", "--blanc", "--blanc", "--rouge", "--rouge"];
const isHP = i => i % 2 === 1;
const HATCH_ALPHA = .3;
// #rrggbb → rgba() avec l'opacité voulue (fond entre les hachures)
const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${n >> 8 & 255},${n & 255},${a})`; };
// Motif hachuré pour Chart.js : fond atténué et rayures à 45° de la couleur pleine
function hatch(color) {
  const r = devicePixelRatio || 1, size = 8, c = document.createElement("canvas");
  c.width = c.height = size * r;
  const g = c.getContext("2d");
  g.scale(r, r); g.fillStyle = rgba(color, HATCH_ALPHA); g.fillRect(0, 0, size, size);
  g.strokeStyle = color; g.lineWidth = 2.5; g.lineCap = "square"; g.beginPath();
  for (const o of [-size, 0, size]) { g.moveTo(o, size); g.lineTo(o + size, 0); }
  g.stroke();
  // Le canvas de Chart.js est déjà mis à l'échelle de la densité : on ramène le motif à sa taille en px CSS
  const pat = g.createPattern(c, "repeat"); pat.setTransform(new DOMMatrix([1 / r, 0, 0, 1 / r, 0, 0]));
  return pat;
}
// Remplissage CSS d'une tranche (barres de répartition)
const trancheFill = i => { const c = css(TRANCHE_COLORS[i]);
  return isHP(i) ? `repeating-linear-gradient(135deg, ${c} 0 2px, ${rgba(c, HATCH_ALPHA)} 2px 4.5px)` : c; };
const QUOTA = { BLEU: "Bleus", BLANC: "Blancs", ROUGE: "Rouges" };
let data = null, charts = {}, currentTab = "charges", view = null;
const openCurves = new Set();  // recharges dépliées, conservées d'un rendu à l'autre
const openMonths = { charges: new Set(), trips: new Set() };  // mois dépliés, par tableau
let seasonOpen = false;  // calendrier Tempo déplié
let offerNotesOpen = false;  // méthode de calcul du comparatif dépliée

// ------------------------------------------------------------------ période
function seasonStart(d) { return new Date(d.getMonth() >= 8 ? d.getFullYear() : d.getFullYear() - 1, 8, 1); }

function periodRange() {
  const p = document.getElementById("preset").value, now = new Date();
  const y = now.getFullYear(), m = now.getMonth();
  const add = (d, days) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + days);
  switch (p) {
    case "season": { const s = seasonStart(now); return [iso(s), iso(add(new Date(s.getFullYear() + 1, 8, 1), -1))]; }
    case "prevseason": { const s = seasonStart(now); return [iso(new Date(s.getFullYear() - 1, 8, 1)), iso(add(s, -1))]; }
    case "last12": return [iso(add(new Date(y - 1, m, now.getDate()), 1)), iso(now)];
    case "year": return [`${y}-01-01`, `${y}-12-31`];
    case "month": return [iso(new Date(y, m, 1)), iso(new Date(y, m + 1, 0))];
    case "prevmonth": return [iso(new Date(y, m - 1, 1)), iso(new Date(y, m, 0))];
    case "custom": return [document.getElementById("from").value || null, document.getElementById("to").value || null];
    default: return [null, null];
  }
}

// Lignes du Sheet restreintes à la période (sur la date de début), et agrégats recalculés comme le Dashboard
function computeView() {
  const [from, to] = periodRange(), all = !from && !to;
  const inRange = s => typeof s === "string" && (!from || s.slice(0, 10) >= from) && (!to || s.slice(0, 10) <= to);
  const charges = data.charges.rows.filter(r => inRange(r[C.start]));
  const trips = data.trips.rows.filter(r => inRange(r[T.start]));
  const months = data.months.filter(m => (!from || m.month >= from.slice(0, 7)) && (!to || m.month <= to.slice(0, 7)));
  let kpi;
  if (all) kpi = data.kpi.map(k => k.value);
  else {
    const withKm = charges.filter(r => num(r[C.km]) > 0), km = sum(charges, C.km);
    const cost = sum(charges, C.cost), kwh = sum(charges, C.kwh);
    kpi = [cost, kwh, km, ratio(sum(withKm, C.cost), km, 100), ratio(sum(withKm, C.kwh), km, 100), ratio(cost, kwh),
           // kWh/100 km voiture : seulement les km des trajets dont la conso est connue (saisies manuelles sans kWh)
           charges.length, sum(trips, T.km), ratio(sum(trips, T.kwh), sum(trips.filter(r => num(r[T.kwh]) > 0), T.km), 100)];
  }
  const tranches = all ? data.tranches.map(t => num(t.kwh))
    : TRANCHES.map((_, i) => sum(charges, C.slots + i) * num(data.loss_coef || 1));
  return { from, to, all, charges, trips, months, kpi, tranches,
           cost: all ? num(data.kpi[0]?.value) : kpi[0], kwh: all ? num(data.kpi[1]?.value) : kpi[1] };
}

function renderPeriod() {
  const { from, to, all } = view;
  document.getElementById("rangeText").textContent = all ? "Toutes les données du Sheet"
    : `${from ? "du " + dshort(from) : "depuis le début"} ${to ? "au " + dshort(to) : "à aujourd'hui"} · ` +
      `${view.charges.length} recharge(s), ${view.trips.length} trajet(s)`;
}

// ------------------------------------------------------------------ KPI + comparaison Base
function renderHealth() {
  const el = document.getElementById("soh"), h = data.health || {};
  el.hidden = data.health === null;  // donnée que la source de la marque ne fournit pas
  if (el.hidden) return;
  if (h.soh == null) { el.innerHTML = `<span class="label">🔋 Santé batterie (SOH)</span><small>indisponible : ${esc(h.error || "pas de relevé")}</small>`; return; }
  el.innerHTML = `<span class="label">🔋 Santé batterie (SOH)</span><span class="n">${fr(h.soh, 0)} %</span>
    <div class="gauge"><span style="width:${Math.min(100, h.soh)}%;background:var(--accent)"></span></div>
    <small>${[h.note && esc(h.note), data.battery_kwh && `batterie ${fr(data.battery_kwh, 0)} kWh neuve`].filter(Boolean).join(" · ")}</small>`;
}

// États de charge (vocabulaire commun à toutes les marques, voir web/vehicle_source.py) -> libellé et couleur
const CHARGE_STATES = { charging: ["En charge", "--good"], finished: ["Terminée", "--bleu"], stopped: ["Arrêtée", "--petrol"],
                        failure: ["Échec", "--bad"], disconnected: ["Débranchée", "--muted"] };

// Charge en cours : la voiture n'envoie son état qu'au début, vers +45 min, à la fin et aux réveils. Entre deux, on
// extrapole depuis le dernier relevé à la vitesse fournie par le serveur (data.charge_estimate), recalculé chaque minute.
const ESTIMATE_MAX_H = 14;  // au-delà, relevé trop ancien pour estimer (charge interrompue ou débranchée ?)
function chargeEstimate() {
  const e = data.charge_estimate;
  if (!e) return null;
  const from = new Date(e.from_at), hours = (Date.now() - from) / 3.6e6;
  if (hours < 5 / 60 || hours > ESTIMATE_MAX_H) return null;  // relevé tout frais, ou trop ancien
  const end = new Date(from.getTime() + Math.max(0, e.target - e.from_level) / e.rate * 3.6e6);
  const level = Math.min(e.target, e.from_level + e.rate * hours), done = Date.now() >= end;
  return { ...e, from, end, done, level, hoursLeft: Math.max(0, (end - Date.now()) / 3.6e6),
           autonomy: e.from_autonomy && e.from_level ? e.from_autonomy * level / e.from_level : null };
}
const hm = d => d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
const hDuration = h => `${Math.floor(h)} h ${String(Math.round(h % 1 * 60) % 60).padStart(2, "0")}`;

// « Marque Modèle » du véhicule (source de la marque ou VEHICLE_NAME) ; « Dashboard VE » tant qu'il est inconnu
function renderTitle() {
  const name = data.vehicle?.name;
  if (!name) return;
  document.getElementById("title").textContent = name;
  document.title = `${name} · Dashboard VE`;
}

// Pied de page : version (lien vers sa note de version) et libellé PREPROD
function renderFooter() {
  const foot = document.getElementById("foot"), v = data.app?.version;
  const ver = document.getElementById("appVersion");
  ver.textContent = v || "";
  if (v) ver.href = `${foot.dataset.repo}/releases/tag/${encodeURIComponent(v)}`;
  document.getElementById("appLabel").innerHTML = data.app?.label ? `<span class="label">${esc(data.app.label)}</span>` : "";
}

// Panneau « Sources et crédits » : sources des données (avec la date de leur dernière lecture), bibliothèques, projet.
// Le bouton retour du téléphone le ferme (entrée d'historique ajoutée à l'ouverture).
const link = (url, text) => `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>`;
const stamp = iso => iso ? new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric",
  hour: "2-digit", minute: "2-digit" }) : "";
function renderSources() {
  const c = data.credit, repo = document.getElementById("foot").dataset.repo, v = data.app?.version;
  // titre, description et, en option, une ligne par détail (date de relève…)
  const item = (title, desc, ...lines) => `<li>${title}<small>${desc}</small>${lines.filter(Boolean).map(l => `<small>${l}</small>`).join("")}</li>`;
  const fuelLines = (data.offers?.fuels || []).filter(f => f.current && !f.current.error).map(f =>
    `${esc(fuelIn(f))} relevé le ${stamp(f.current.fetched_at)} (${f.current.stations} stations, ${esc(f.current.zone)})`);
  const days = Object.values(data.fuel_history || {}).reduce((n, h) => Math.max(n, Object.keys(h).length), 0);
  const sources = [
    c?.name && /^https:\/\//.test(c.url || "") ? item(`Véhicule${c.group ? ` ${esc(c.group)}` : ""} : ${link(c.url, c.name)}`,
      esc(c.provides || "données du véhicule"), data.vehicle?.name ? esc(data.vehicle.name) : "") : "",
    item("Google Sheet", "calcul des coûts (recharges, trajets, indicateurs, totaux par mois)", data.fetched_at ? `relu le ${stamp(data.fetched_at)}` : ""),
    item(`Tempo : ${link("https://www.api-couleur-tempo.fr", "api-couleur-tempo.fr")}`, "couleur des jours et saison Tempo",
      data.season?.fetched_at ? `relu le ${stamp(data.season.fetched_at)}` : ""),
    data.offers?.flex_days ? item(`Zen Flex : ${link("https://particulier.edf.fr/fr/accueil/electricite-gaz/zen-flex.html", "particulier.edf.fr")}`,
      "calendrier des jours Éco, Sobriété et Bonus (API du site EDF, non documentée)",
      data.offers.flex_days.fetched_at ? `relu le ${stamp(data.offers.flex_days.fetched_at)} (${Object.keys(data.offers.flex_days.days).length} jours connus)`
        : esc(data.offers.flex_days.error || "")) : "",
    item(`Carburants : ${link("https://www.prix-carburants.gouv.fr/rubrique/opendata/", "prix-carburants.gouv.fr")}`,
      "prix moyens du jour et archives journalières officielles" + (days ? ` (${days} jour${days > 1 ? "s" : ""} de recharge)` : ""), ...fuelLines),
  ].join("");
  document.getElementById("sourcesBody").innerHTML = `
    <h3>Données</h3><ul>${sources}</ul>
    <h3>Bibliothèques</h3><ul>${item(link("https://www.chartjs.org", "Chart.js") + " 4.4.1", "graphiques · " +
      link("https://github.com/chartjs/Chart.js/blob/master/LICENSE.md", "licence MIT") + ", © Chart.js Contributors")}</ul>
    <h3>Projet</h3><ul>${item(`Dashboard VE ${v ? link(`${repo}/releases/tag/${encodeURIComponent(v)}`, v) : ""}`,
      `${link(repo, "code source")} · ${link(repo + "/blob/main/LICENSE", "licence MIT")} · © 2026 Demonic31`)}</ul>`;
}
// Nouveautés : journal des versions lu une fois (/api/data ne le transporte pas), chaque version liée à sa note GitHub
let changelog = null;
const mdInline = t => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");
async function renderChangelog() {
  const body = document.getElementById("changelogBody"), repo = document.getElementById("foot").dataset.repo, cur = data.app?.version;
  if (!changelog) {
    body.innerHTML = '<div class="empty">Chargement…</div>';
    try {
      const resp = await fetch("api/changelog");
      if (!resp.ok) throw new Error(resp.status);
      changelog = await resp.json();
    } catch (e) { body.innerHTML = `<div class="empty">Journal indisponible : ${esc(e.message)}</div>`; return; }
  }
  const day = d => /^\d{4}-\d{2}-\d{2}$/.test(d) ? new Date(d + "T12:00").toLocaleDateString("fr-FR") : esc(d);
  body.innerHTML = (changelog.length ? changelog.map(v => `<section class="release"><h3>${esc(v.version)}
      ${v.version === cur ? '<span class="current">installée</span>' : ""}<small>${day(v.date)}</small>
      <span class="gh-link">${link(`${repo}/releases/tag/${encodeURIComponent(v.version)}`, "Note de version")}</span></h3>
      <ul>${v.items.map(i => `<li>${mdInline(i)}</li>`).join("")}</ul></section>`).join("")
    : '<div class="empty">Aucune version dans le journal</div>') +
    `<p class="all">${link(repo + "/releases", "Toutes les versions sur GitHub")}</p>`;
}

// Panneaux (Sources et crédits, Nouveautés) : fermés par ✕, Échap, clic sur le fond ou bouton retour du téléphone
// (entrée d'historique ajoutée à l'ouverture)
function setupDialog(id, openerId, closeId, render) {
  const dlg = document.getElementById(id);
  document.getElementById(openerId).addEventListener("click", e => {
    e.preventDefault();
    if (!data || dlg.open) return;
    render(); dlg.showModal(); history.pushState({ dialog: id }, "");
  });
  const close = () => { if (dlg.open) history.state?.dialog === id ? history.back() : dlg.close(); };
  document.getElementById(closeId).addEventListener("click", close);
  dlg.addEventListener("click", e => { if (e.target === dlg) close(); });  // clic sur le fond
  dlg.addEventListener("cancel", e => { e.preventDefault(); close(); });   // touche Échap
  window.addEventListener("popstate", () => { if (dlg.open) dlg.close(); });
}
setupDialog("sources", "openSources", "closeSources", renderSources);
setupDialog("changelog", "appVersion", "closeChangelog", renderChangelog);

function renderCar() {
  const el = document.getElementById("car"), c = data.car || {};
  el.hidden = data.car === null;  // donnée que la source de la marque ne fournit pas
  if (el.hidden) return;
  if (c.error || c.level == null) {
    document.getElementById("carAt").textContent = "";
    document.getElementById("carItems").innerHTML = `<div class="empty">État indisponible : ${esc(c.error || "pas de relevé")}</div>`;
    return;
  }
  const est = c.status === "charging" ? chargeEstimate() : null;
  const real = Math.max(0, Math.min(100, num(c.level))), lvl = est ? est.level : real;
  const at = c.updated_at ? new Date(c.updated_at.replace(" ", "T")) : null;
  const atText = d => d.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  document.getElementById("carAt").textContent = at ? "dernier relevé le " + atText(at) : "";
  const [label, color] = est?.done ? ["Probablement terminée", "--bleu"] : CHARGE_STATES[c.status] || [c.status || "Inconnue", "--muted"];
  let extra = "";
  if (est) extra = est.done ? `fin estimée vers ${hm(est.end)}`
    : `fin dans ≈ ${hDuration(est.hoursLeft)} (vers ${hm(est.end)})` + (est.car_end_at ? `\nla voiture annonçait ${hm(new Date(est.car_end_at))}` : "");
  else if (c.status === "charging") extra = [c.charging_rate ? `${fr(c.charging_rate, 0)} km/h` : "",
    c.remaining_hours ? `fin dans ${hDuration(c.remaining_hours)}` : ""].filter(Boolean).join(" · ");
  const approx = est ? "≈ " : "";
  document.getElementById("carItems").innerHTML = `
    <div><div class="label">Niveau</div><div class="n" style="color:hsl(${1.2 * lvl} 70% var(--lvl-l))">${approx}${fr(lvl, 0)} %</div>
      <div class="level"><span style="width:${lvl}%;background-size:${lvl ? 10000 / lvl : 100}% 100%"></span>${est && lvl > real
        ? `<i class="est" style="left:${real}%;width:${lvl - real}%"></i>` : ""}</div></div>
    <div><div class="label">Autonomie estimée</div><div class="n">${approx}${fr(est?.autonomy ?? c.autonomy, 0)} <small>km</small></div></div>
    <div><div class="label">Charge</div><span class="state" style="--c:var(${color})">${esc(label)}</span>${extra ? `<div class="sub">${esc(extra).replace("\n", "<br>")}</div>` : ""}</div>
    <div><div class="label">Compteur</div><div class="n">${fr(c.mileage, 0)} <small>km</small></div></div>
    ${est ? `<div class="estimate">⏱️ <b>Estimation</b> : dernière synchro à ${hm(est.from)} (${fr(est.from_level, 0)} %). Niveau calculé à
      ${fr(est.rate, 1)} %/h (${esc(est.source)}) ; recalé à la prochaine synchro (réveil ou fin de charge).</div>` : ""}`;
}

// Dernière recharge connue (indépendante de la période), sous le bloc Voiture : ligne de résumé dépliable,
// ouverte par défaut sur grand écran ; l'état choisi est conservé lors des actualisations
let lastChargeOpen = matchMedia("(min-width: 641px)").matches;
function lastCharge() {
  return data.charges.rows.reduce((a, r) => r[C.start] && (!a || new Date(r[C.start]) > new Date(a[C.start])) ? r : a, null);
}
function lastChargeItems(r) {
  const start = new Date(r[C.start]), end = r[2] ? new Date(r[2]) : null, eqs = fuelEqs(r);
  const pct = data.battery_kwh && num(r[C.kwhBat]) ? ` <small>(${fr(100 * num(r[C.kwhBat]) / data.battery_kwh, 0)} %)</small>` : "";
  return `
    <div><div class="label">Durée</div><div class="n">${hDuration(num(r[C.duration]))}</div>
      <div class="sub">${hm(start)}${end ? " → " + hm(end) : ""}</div></div>
    <div><div class="label">Niveau</div><div class="n">${fr(r[3], 0)} → ${fr(r[4], 0)} <small>%</small></div>
      <div class="sub">${r[C.color] ? `<span class="pill ${esc(r[C.color])}">${esc(r[C.color])}</span>` : ""}</div></div>
    <div><div class="label">Énergie voiture</div><div class="n">${fr(r[C.kwhBat], 1)} <small>kWh</small>${pct}</div>
      <div class="sub">${fr(r[C.kwh], 1)} kWh facturés</div></div>
    <div><div class="label">Coût</div><div class="n cost">${eur(r[C.cost])}</div>
      <div class="sub lc-price"><span>${fr(r[C.eurKwh], 4)} €/kWh${eqs.length ? '<span class="dot"> ·</span>' : ""}</span>${eqs.length ? `<span class="fuels">${eqs.map(fuelTag).join("")}</span>` : ""}</div></div>
    <div class="slots"><div class="label">Heures creuses / pleines</div>${slotBar(r.slice(C.slots, C.slots + 6).map(Number))}</div>`;
}
// Part des heures pleines d'une recharge (#50) : « dont 12 % en HP », couleur de la tranche HP dominante ; rien si 100 % HC
const HP_TEXT = ["--bleu", "--blanc-txt", "--rouge"];
function hpNote(r) {
  const slots = r.slice(C.slots, C.slots + 6).map(Number), costs = trancheCosts(r).cost;
  const total = slots.reduce((a, b) => a + (b || 0), 0), hp = slots.filter((_, i) => isHP(i));
  const hpKwh = hp.reduce((a, b) => a + (b || 0), 0), hpCost = costs.filter((_, i) => isHP(i)).reduce((a, b) => a + b, 0);
  if (!total || !(hpKwh > 0)) return "";
  const pct = 100 * hpKwh / total, top = hp.indexOf(Math.max(...hp));
  const tip = `Heures pleines : ${fr(hpKwh, 2)} kWh sur ${fr(total, 2)} kWh · ${eur(hpCost)}` +
    hp.map((k, j) => k > 0 ? `\n${TRANCHES[2 * j + 1]} : ${fr(k, 2)} kWh · ${eur(costs[2 * j + 1])}` : "").join("");
  return `<span class="lc-hp" style="color:var(${HP_TEXT[top]})" title="${esc(tip)}">dont ${pct < 1 ? "< 1" : fr(pct, 0)} % en HP</span>`;
}
function renderLastCharge() {
  const el = document.getElementById("lastCharge"), r = lastCharge();
  el.hidden = !r;
  if (!r) return;
  const slots = r.slice(C.slots, C.slots + 6).map(Number);
  const when = new Date(r[C.start]).toLocaleString("fr-FR", { weekday: "short", day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit" });
  el.innerHTML = `<button type="button" class="lc-summary" aria-expanded="${lastChargeOpen}"><span class="lc-head"><span class="chev">▸</span>
    <b>🔌 Dernière recharge</b> <span class="when">${when}</span></span><span class="lc-stats"><span>${hDuration(num(r[C.duration]))}</span>
    <span>${fr(r[3], 0)} → ${fr(r[4], 0)} %</span><span>${fr(r[C.kwhBat], 1)} kWh</span><b class="cost">${eur(r[C.cost])}</b></span></button>
    ${lastChargeOpen ? "" : `<div class="lc-bar"><span>HC/HP</span>${slotBar(slots)}${hpNote(r)}</div>`}
    <div class="items"${lastChargeOpen ? "" : " hidden"}>${lastChargeItems(r)}</div>`;
  const toggle = () => { lastChargeOpen = !lastChargeOpen; renderLastCharge(); };
  el.querySelector(".lc-summary").addEventListener("click", toggle);
  el.querySelector(".lc-bar")?.addEventListener("click", toggle);
}

// Ordre d'affichage des tuiles (indices des colonnes du Dashboard) : coût, énergie, consommation, distance
const KPI_ORDER = [0, 3, 1, 5, 4, 8, 2, 7, 6];

function renderKpis() {
  const cards = KPI_ORDER.filter(i => i < data.kpi.length).map(i =>
    `<div class="card kpi${i === 0 || i === 3 ? " main" : ""}"><div class="label">${esc(data.kpi[i].label)}</div>
     <div class="value">${(KPI_FMT[i] || (v => esc(v)))(view.kpi[i])}</div></div>`);
  document.getElementById("kpis").innerHTML = cards.join("");
}

// ------------------------------------------------------------------ comparatif des offres
// Jours fériés français (fixes + lundi de Pâques, Ascension, lundi de Pentecôte)
const holidays = {};
function isHoliday(d) {
  const y = d.getFullYear();
  if (!holidays[y]) {
    // Dimanche de Pâques (algorithme de Meeus/Jones/Butcher)
    const a = y % 19, b = Math.floor(y / 100), c = y % 100, d4 = Math.floor(b / 4), e = b % 4;
    const f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d4 - g + 15) % 30;
    const i = Math.floor(c / 4), k = c % 4, l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
    const month = Math.floor((h + l - 7 * m + 114) / 31), day = (h + l - 7 * m + 114) % 31 + 1;
    const easter = n => iso(new Date(y, month - 1, day + n));
    holidays[y] = new Set([`${y}-01-01`, `${y}-05-01`, `${y}-05-08`, `${y}-07-14`, `${y}-08-15`, `${y}-11-01`, `${y}-11-11`, `${y}-12-25`,
                           easter(1), easter(39), easter(50)]);
  }
  return holidays[y].has(iso(d));
}

// Part du temps de charge passée au prix bas (échantillon toutes les 5 min), null sans heure de fin
function lowShare(r, isLow) {
  const start = new Date(r[C.start]), end = new Date(r[2]);
  if (!(end > start)) return null;
  const step = 5 * 60e3;
  let low = 0, n = 0;
  for (let t = start.getTime() + step / 2; t < end.getTime(); t += step, n++) if (isLow(new Date(t))) low++;
  return n ? low / n : null;
}
const hourOf = d => d.getHours() + d.getMinutes() / 60;
const inHours = (h, [a, b]) => a > b ? h >= a || h < b : h >= a && h < b;
// Option Heures creuses, plages saisonnalisées (Enedis, déploiement fin 2025 – 2027 ; 8 h de HC par jour).
// Plages réelles fixées pour chaque compteur Linky : on retient 23h–7h l'hiver, et l'été 5 h la nuit (1h–6h)
// plus 3 h l'après-midi (placées dans la fenêtre 11h–17h, sans effet sur des recharges de nuit).
const HCHP_SEASONS = { winter: { emoji: "❄️", months: "nov.–mars", hours: [[23, 7]] },
                       summer: { emoji: "☀️", months: "avr.–oct.", hours: [[1, 6], [13, 16]] } };
const hchpSeason = d => d.getMonth() >= 3 && d.getMonth() <= 9 ? HCHP_SEASONS.summer : HCHP_SEASONS.winter;
const hchpLow = d => hchpSeason(d).hours.some(h => inHours(hourOf(d), h));
const hoursLabel = hours => hours.map(([a, b]) => `${a}h–${b % 24}h`).join(", ").replace(/, ([^,]*)$/, " et $1");
// Zen Flex : mêmes plages tous les jours (HP 8h–13h et 18h–20h, 17 h creuses) ; 20 jours Sobriété par an en semaine,
// annoncés la veille. Calendrier lu par le serveur dans l'API EDF (offers.flex_days : {jour: eco|sobriete|bonus}) ;
// un jour Sobriété n'est possible qu'en semaine, du 15 octobre au 15 avril ; un jour possible pas encore relu est
// approché par la couleur Tempo (Rouge = Sobriété)
const FLEX_HC = [[0, 8], [13, 18], [20, 24]];
const flexHc = d => FLEX_HC.some(h => inHours(hourOf(d), h));
const flexPossible = day => { const d = new Date(day + "T12:00:00"), md = day.slice(5);
  return d.getDay() % 6 !== 0 && !isHoliday(d) && (md <= "04-15" || md >= "10-15"); };
function flexSobriety() {
  const known = data.offers?.flex_days?.days || {};
  const red = new Set((data.colors || []).filter(c => c.color === "ROUGE").map(c => c.day));
  const guessed = new Set();  // jours approchés par Tempo
  return { guessed, isSob: day => {
    if (!flexPossible(day)) return false;
    if (day in known) return known[day] === "sobriete";
    guessed.add(day); return red.has(day);
  } };
}

// Coût de chaque tranche d'une recharge (#44, #50) : kWh × pertes × prix de la grille Tempo en vigueur à sa date,
// ramené au coût calculé par le Sheet (au prorata des kWh si la grille manque)
function trancheCosts(r) {
  const coef = num(data.loss_coef || 1), day = String(r[C.start]).slice(0, 10);
  const kwh = TRANCHES.map((_, i) => num(r[C.slots + i]) * coef);
  const grid = (data.tempo_prices || []).filter(g => g.since <= day), g = grid[grid.length - 1];
  const raw = g ? kwh.map((k, i) => k * num([g.bleu, g.blanc, g.rouge][i >> 1][i & 1])) : kwh;
  const tot = raw.reduce((a, b) => a + b, 0), cost = num(r[C.cost]);
  return { kwh, cost: raw.map(v => tot ? v * cost / tot : 0) };
}

// Tarif Tempo en vigueur à une date (dernière ligne de la grille du Sheet dont la date d'effet est passée) :
// une ligne par couleur, HP puis HC, puis la répartition de la période (mix) si fournie
function tempoPrices(day, mix = "") {
  const grid = (data.tempo_prices || []).filter(g => !day || g.since <= day), g = grid[grid.length - 1];
  if (!g) return "ton contrat (prix de la grille Tempo du Sheet)" + (mix && "\n" + mix);
  const [hcS, hcE] = data.hc_hours, p = v => fr(v, 4);
  return [["🔵", g.bleu], ["⚪", g.blanc], ["🔴", g.rouge]]
    .map(([dot, [hc, hp]]) => `${dot} HP ${p(hp)} · HC ${hcS}h–${hcE}h ${p(hc)} €/kWh`).join("\n") +
    (mix && "\n" + mix) + `\nTarif en vigueur depuis le ${dshort(g.since)}`;
}

function renderOffers() {
  const o = data.offers || {}, coef = num(data.loss_coef || 1), rows = view.charges;
  // kWh facturés de chaque recharge répartis entre HC et HP de l'option Heures creuses (au prorata du temps de charge ;
  // sans heure de fin, répartition HC/HP Tempo de la recharge)
  let hcKwh = 0, hpKwh = 0;
  rows.forEach(r => {
    const kwh = [0, 1, 2, 3, 4, 5].reduce((s, i) => s + num(r[C.slots + i]), 0) * coef;
    const tempoHc = num(r[C.slots]) + num(r[C.slots + 2]) + num(r[C.slots + 4]);
    const share = lowShare(r, hchpLow) ?? (kwh ? tempoHc * coef / kwh : 0);
    hcKwh += kwh * share; hpKwh += kwh * (1 - share);
  });
  // km de l'équivalent essence : trajets enregistrés ou compteur entre recharges, le plus grand des deux
  const odoKm = num(view.kpi[2]), tripKm = num(view.kpi[7]), km = Math.max(odoKm, tripKm), [hcS, hcE] = data.hc_hours;
  const kmSource = !km ? "aucun trajet ni relevé compteur" : tripKm >= odoKm ? "trajets enregistrés" : "compteur entre recharges";
  const hcLabel = `HC ${hcS}h–${hcE}h`;
  // €/100 km : Tempo = indicateur du Sheet ; offres électriques au même kilométrage (au prorata du coût) ;
  // carburants = L/100 km × prix moyen, indépendant du kilométrage
  const ref = view.cost, ref100 = typeof view.kpi[3] === "number" ? view.kpi[3] : null;
  const elec100 = cost => ref100 != null && ref ? ref100 * cost / ref : null;
  // répartition Tempo de la période : part HC, puis part de chaque couleur de jour (couleurs absentes omises)
  const ts = TRANCHES.map((_, i) => rows.reduce((s, r) => s + num(r[C.slots + i]), 0)), tTot = ts.reduce((a, b) => a + b, 0);
  const tempoMix = tTot ? `${Math.round(100 * (ts[0] + ts[2] + ts[4]) / tTot)} % en heures creuses · ` +
    [["🔵", 0], ["⚪", 2], ["🔴", 4]].map(([dot, i]) => [dot, Math.round(100 * (ts[i] + ts[i + 1]) / tTot)])
      .filter(([, p]) => p).map(([dot, p]) => `${dot} ${p} %`).join(" ") + " sur la période" : "";
  const list = [{ name: "Tempo", sub: tempoPrices(view.to, tempoMix), cost: ref, per100: ref100, color: "--accent", group: "ref" }];
  const elec = (name, sub, cost, color) => ({ name, sub, cost, per100: elec100(cost), color, group: "elec" });
  list.push(o.base ? elec("Base", `${fr(o.base, 4)} €/kWh`, view.kwh * o.base, "--base")
                   : { name: "Base", missing: "BASE_PRICE_KWH", group: "elec" });
  list.push(o.hchp?.hc && o.hchp?.hp
    ? elec("Heures creuses", Object.values(HCHP_SEASONS).reverse().map(z =>
             `${z.emoji} ${z.months} HP ${fr(o.hchp.hp, 4)} · HC ${hoursLabel(z.hours)} ${fr(o.hchp.hc, 4)} €/kWh`).join("\n") +
           (hcKwh + hpKwh ? `\n${fr(100 * hcKwh / (hcKwh + hpKwh), 0)} % en heures creuses sur la période` : "") +
           "\nPlages indicatives : les heures creuses sont fixées par Enedis pour chaque compteur Linky (voir Méthode de calcul)",
           hcKwh * o.hchp.hc + hpKwh * o.hchp.hp, "--bleu")
    : { name: "Heures creuses", missing: "HCHP_HC_PRICE_KWH / HCHP_HP_PRICE_KWH", group: "elec" });
  // part de chaque catégorie d'une offre sur la période, en %, catégories vides omises
  const split = (kwh, labels) => { const tot = kwh.reduce((a, b) => a + b, 0);
    return tot ? "\n" + labels.map((l, i) => [Math.round(100 * kwh[i] / tot), l]).filter(([p]) => p)
      .map(([p, l]) => `${p} % ${l}`).join(", ") + " sur la période" : ""; };
  if (o.zen?.low && o.zen?.high) {
    // kWh facturés répartis au prorata du temps de charge : semaine HC, week-end ou férié, semaine HP ;
    // sans heure de fin, jour de début et tranches HC/HP Tempo de la recharge
    const zk = [0, 0, 0];
    rows.forEach(r => {
      const kwh = num(r[C.kwh]), w = [0, 0, 0], start = new Date(r[C.start]), end = new Date(r[2]), step = 5 * 60e3;
      if (end > start) for (let t = start.getTime() + step / 2; t < end.getTime(); t += step) {
        const d = new Date(t);
        w[d.getDay() % 6 === 0 || isHoliday(d) ? 1 : inHours(hourOf(d), data.hc_hours.map(Number)) ? 0 : 2]++;
      }
      const n = w[0] + w[1] + w[2], bat = [0, 1, 2, 3, 4, 5].reduce((a, i) => a + num(r[C.slots + i]), 0);
      const hc = bat ? (num(r[C.slots]) + num(r[C.slots + 2]) + num(r[C.slots + 4])) / bat : 1;
      const f = n ? w.map(x => x / n) : start.getDay() % 6 === 0 || isHoliday(start) ? [0, 1, 0] : [hc, 0, 1 - hc];
      f.forEach((x, i) => { zk[i] += kwh * x; });
    });
    list.push(elec("Zen Week-End + HC", `💼 Semaine HP ${fr(o.zen.high, 4)} · ${hcLabel} ${fr(o.zen.low, 4)} €/kWh` +
      `\n🏖️ Week-end et jours fériés ${fr(o.zen.low, 4)} €/kWh toute la journée` +
      split(zk, ["en heures creuses", "le week-end ou un jour férié", "en heures pleines"]),
      (zk[0] + zk[1]) * o.zen.low + zk[2] * o.zen.high, "--blanc-hc"));
  } else list.push({ name: "Zen Week-End + HC", missing: "ZEN_LOW_PRICE_KWH / ZEN_HIGH_PRICE_KWH", group: "elec" });
  const fx = o.zen_flex;
  if (fx && [...fx.eco, ...fx.sob].every(v => v)) {
    // kWh facturés répartis au prorata du temps de charge entre HC/HP et jour Éco/Sobriété (5 min par 5 min) ;
    // sans heure de fin, tranches Tempo de la recharge (Rouge = Sobriété)
    const flex = flexSobriety();
    let cost = 0;
    const fk = [0, 0, 0, 0];
    rows.forEach(r => {
      const slots = [0, 1, 2, 3, 4, 5].map(i => num(r[C.slots + i]) * coef), kwh = slots.reduce((a, b) => a + b, 0);
      const start = new Date(r[C.start]), end = new Date(r[2]), step = 5 * 60e3, w = [0, 0, 0, 0];  // éco HC/HP, sob HC/HP
      if (end > start) for (let t = start.getTime() + step / 2; t < end.getTime(); t += step) {
        const d = new Date(t), sob = flex.isSob(iso(d));
        w[(sob ? 2 : 0) + (flexHc(d) ? 0 : 1)]++;
      }
      const n = w.reduce((a, b) => a + b, 0);
      // sans heure de fin : tranches Tempo de la recharge, jour Sobriété selon le calendrier (Rouge sinon)
      const sobDay = typeof r[C.start] === "string" && flex.isSob(r[C.start].slice(0, 10));
      const k = n ? w.map(x => kwh * x / n) : sobDay ? [0, 0, slots[0] + slots[2] + slots[4], slots[1] + slots[3] + slots[5]]
                                                  : [slots[0] + slots[2] + slots[4], slots[1] + slots[3] + slots[5], 0, 0];
      cost += k[0] * fx.eco[0] + k[1] * fx.eco[1] + k[2] * fx.sob[0] + k[3] * fx.sob[1];
      k.forEach((x, i) => { fk[i] += x; });
    });
    list.push(elec("Zen Flex", `🟢 Éco HP ${fr(fx.eco[1], 4)} · HC ${hoursLabel(FLEX_HC)} ${fr(fx.eco[0], 4)} €/kWh` +
      `\n🟠 Sobriété HP ${fr(fx.sob[1], 4)} · HC ${fr(fx.sob[0], 4)} €/kWh` +
      split(fk, ["en Éco HC", "en Éco HP", "en Sobriété HC", "en Sobriété HP"]) +
      (flex.guessed.size ? `\nJours Sobriété : calendrier EDF, ${flex.guessed.size} jour(s) pas encore relu(s) approché(s) par Tempo (voir Méthode de calcul)`
                         : "\nJours Sobriété : calendrier EDF (voir Méthode de calcul)"), cost, "--flex"));
  } else list.push({ name: "Zen Flex", missing: "ZEN_FLEX_ECO_HC_PRICE_KWH / ZEN_FLEX_ECO_HP_PRICE_KWH / ZEN_FLEX_SOB_HC_PRICE_KWH / ZEN_FLEX_SOB_HP_PRICE_KWH", group: "elec" });
  (o.fuels?.length ? o.fuels : [{ key: "sp98", name: "SP98" }]).forEach(f => {
    const cur = f.current, color = f.key === "gazole" ? "--diesel" : "--petrol";
    if (num(f.l100) && cur && !cur.error)
      list.push({ name: fuelLabel(f), sub: `${fr(f.l100, 1)} L/100 km × ${fr(cur.price, 3)} €/L (moyenne ${cur.zone}, ` +
        `${cur.stations} stations, ${cur.days} derniers jours, relevé le ${new Date(cur.fetched_at).toLocaleDateString("fr-FR")})`,
        cost: km * f.l100 / 100 * cur.price, per100: f.l100 * cur.price, color, group: "fuel" });
    else list.push({ name: fuelLabel(f), missing: cur?.error ? "prix carburant indisponible : " + cur.error
      : `la consommation dans PETROL_FUELS (ex. ${f.key}:${f.key === "gazole" ? "5.5" : "7"})`, raw: !!cur?.error, group: "fuel" });
  });

  // Tempo en tête, puis chaque groupe du moins cher au plus cher (tri stable : ordre d'origine en cas d'égalité),
  // les offres à renseigner en fin de groupe
  const byCost = (a, b) => (a.missing ? 1 : 0) - (b.missing ? 1 : 0) || (a.missing ? 0 : a.cost - b.cost);
  const group = g => list.filter(x => x.group === g).sort(byCost);
  const elecs = [list[0], ...group("elec")], fuelsSorted = group("fuel");
  const priced = elecs.filter(x => !x.missing);
  const cheapest = priced.length > 1 ? priced.reduce((a, b) => b.cost < a.cost - 0.005 ? b : a) : null;

  const max = Math.max(...list.map(x => x.cost || 0)) || 1;
  const delta = (v, r) => {
    if (v == null || r == null) return "";
    const d = v - r, pct = r ? 100 * d / r : 0, s = d >= 0 ? "+" : "−";
    return `<small class="delta ${d > 0.005 ? "up" : d < -0.005 ? "down" : ""}">${s}${eur(Math.abs(d))} (${s}${fr(Math.abs(pct), 0)} %)</small>`;
  };
  const line = x => {
    if (x.missing) return `<div class="offer"><div class="name"><span class="nm">${x.name}</span></div><div class="missing">${x.raw ? esc(x.missing) : `Renseigner ${esc(x.missing)} dans .env`}</div></div>`;
    const isRef = x.group === "ref";
    const badge = (isRef ? `<span class="badge cur" title="Ton abonnement actuel, référence des écarts">offre actuelle</span>` : "") +
      (x === cheapest ? `<span class="badge" title="Offre d'électricité la moins chère sur la période">la moins chère</span>` : "");
    // détail du tarif en info-bulle au survol, ou affiché sous le nom d'un appui sur la ligne (écrans tactiles)
    return `<div class="offer${isRef ? " ref" : ""}" title="${esc(x.sub)}"><div class="name"><span class="nm">${x.name}</span>${badge && `<span class="badges">${badge}</span>`}<small>${esc(x.sub)}</small></div>
      <div class="track"><span style="width:${100 * x.cost / max}%;background:${css(x.color)}"></span></div>
      <div class="cost">${eur(x.cost)}${isRef ? "" : delta(x.cost, ref)}</div>
      <div class="per100">${x.per100 == null ? "–" : eur(x.per100)}${isRef ? "" : delta(x.per100, ref100)}</div></div>`;
  };
  const el = document.getElementById("offers");
  el.innerHTML =
    `<div class="offer sep first"><span>⚡ Abonnements EDF</span><span class="h-bar"></span><span class="cost">Coût</span><span class="per100">€/100 km</span></div>` +
    elecs.map(line).join("") + `<div class="offer sep"><span>⛽ Équivalents thermiques</span></div>` + fuelsSorted.map(line).join("") +
    `<details class="cal notes-fold"${offerNotesOpen ? " open" : ""}><summary><span class="chev">▸</span> Méthode de calcul</summary><ul class="notes">
    <li>Coût de l'énergie seule (kWh facturés = kWh batterie × ${fr(coef, 2)}), abonnements non compris ; écarts calculés par rapport à Tempo.</li>
    <li>Heures creuses : nouvelles plages saisonnalisées, déployées par Enedis de fin 2025 à 2027 (8 h creuses par jour, mêmes prix été comme hiver).
      Plages retenues ici : hiver (nov.–mars) ${hoursLabel(HCHP_SEASONS.winter.hours)} ; été (avr.–oct.) ${hoursLabel(HCHP_SEASONS.summer.hours)},
      les 3 h de l'après-midi placées arbitrairement dans la fenêtre 11h–17h, sans effet sur des recharges de nuit.
      Les heures creuses réelles sont fixées par Enedis pour chaque compteur Linky et diffèrent d'un logement à l'autre : le résultat reste une estimation.</li>
    <li>Zen suppose les mêmes heures creuses que Tempo (${hcLabel}, onglet Paramètres).</li>
    <li>Zen Flex : 17 h creuses par jour (HP 8h–13h et 18h–20h), et 20 jours Sobriété par an en semaine, de mi-octobre à mi-avril, annoncés la veille par EDF.
      Leur calendrier est lu dans l'API du site EDF (non documentée, susceptible de changer) et gardé en cache ;
      un jour pas encore relu est approché par la couleur Tempo (Rouge = Sobriété).</li>
    <li>Heures creuses, Zen et Zen Flex sont répartis au prorata du temps de charge.</li>
    <li>€/100 km : Tempo selon l'indicateur du Sheet (km compteur entre recharges) ; les autres offres d'électricité au même kilométrage ; carburants = consommation × prix moyen.</li>
    <li>Coût des carburants : ${fr(km, 0)} km parcourus sur la période (${kmSource}) ; prix moyen du carburant relu chaque jour.</li></ul></details>`;
  el.querySelector("details").addEventListener("toggle", e => { offerNotesOpen = e.target.open; });
  el.onclick = e => { const row = e.target.closest(".offer[title]"); if (row) row.classList.toggle("show"); };
}

// ------------------------------------------------------------------ graphiques
function chart(id, config) {
  charts[id]?.destroy();
  charts[id] = new Chart(document.getElementById(id), config);
}

// Pas de temps du graphique des coûts (#44) : choisi, ou automatique selon la durée de la période
const STEP_NAMES = { day: "jour", week: "semaine", month: "mois", year: "année" };
let costStep = "auto";
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const dayLabel = (s, o) => new Date(s + "T12:00:00").toLocaleDateString("fr-FR", o);
// Bornes du graphique : début de la période (sinon première recharge), fin de la période plafonnée à aujourd'hui
function costBounds() {
  const today = iso(new Date());
  const first = data.charges.rows.map(r => r[C.start]).filter(v => typeof v === "string").sort()[0]?.slice(0, 10) || today;
  const from = view.from || first, end = view.to || today;
  return { from, to: end < today ? end : today, days: Math.round((new Date(end) - new Date(from)) / 864e5) + 1 };
}
function autoStep(days) { return days <= 31 ? "day" : days <= 92 ? "week" : days <= 1096 ? "month" : "year"; }

// Jour ou semaine (lundi → dimanche) : coût de chaque tranche, recharge rattachée à son jour de début ; jours vides à 0
function dayBuckets(step, from, to, rows) {
  let d = new Date(from + "T12:00:00");
  if (step === "week") d = addDays(d, -((d.getDay() + 6) % 7));
  const out = [];
  for (; iso(d) <= to; d = addDays(d, step === "week" ? 7 : 1)) {
    const a = iso(d), b = iso(step === "week" ? addDays(d, 6) : d);
    out.push({ key: a, from: a < from ? from : a, to: b > to ? to : b, full: [a, b],
               cost: TRANCHES.map(() => 0), kwh: TRANCHES.map(() => 0) });
  }
  rows.forEach(r => {
    const day = r[C.start].slice(0, 10), bk = out.find(x => x.full[0] <= day && day <= x.full[1]);
    if (!bk) return;
    const t = trancheCosts(r);
    t.cost.forEach((v, i) => { bk.cost[i] += v; bk.kwh[i] += t.kwh[i]; });
  });
  return out;
}
// Année : somme des mois du Sheet, €/100 km recalculé sur les mois où des km sont connus
function yearBuckets(months) {
  const by = new Map();
  months.forEach(m => { const y = m.month.slice(0, 4); by.has(y) || by.set(y, []); by.get(y).push(m); });
  return [...by].map(([y, ms]) => {
    const withKm = ms.filter(m => num(m.km) > 0), km = withKm.reduce((a, m) => a + num(m.km), 0);
    return { label: y, cost: ms.reduce((a, m) => a + num(m.cost), 0), eur_100km: ratio(withKm.reduce((a, m) => a + num(m.cost), 0), km, 100),
             note: ms.length < 12 ? `Mois couverts : ${monthShort(ms[0].month)} à ${monthShort(ms[ms.length - 1].month)}` : "" };
  });
}
const monthShort = m => new Date(m + "-01T12:00:00").toLocaleDateString("fr-FR", { month: "short", year: "numeric" });

// Pas forcé plus large que le pas automatique : la fenêtre s'élargit pour montrer plusieurs barres,
// en finissant à la fin de la période (12 semaines, 12 mois ou toutes les années)
const STEP_ORDER = ["day", "week", "month", "year"];
function widen(step, b) {
  const end = new Date(b.to + "T12:00:00"), toM = b.to.slice(0, 7), until = b.to === iso(new Date()) ? "" : " jusqu'à ";
  if (step === "week") {
    const d = addDays(end, -7 * 11), from = iso(addDays(d, -((d.getDay() + 6) % 7)));  // lundi, pour 12 semaines entières
    return { from, charges: data.charges.rows.filter(r => typeof r[C.start] === "string" && r[C.start].slice(0, 10) >= from && r[C.start].slice(0, 10) <= b.to),
             note: until ? `12 semaines jusqu'au ${dayLabel(b.to, { day: "numeric", month: "short" })}` : "12 dernières semaines" };
  }
  if (step === "month") {
    const fromM = iso(new Date(end.getFullYear(), end.getMonth() - 11, 1)).slice(0, 7);
    return { months: data.months.filter(m => m.month >= fromM && m.month <= toM),
             note: until ? `12 mois jusqu'à ${monthShort(toM)}` : "12 derniers mois" };
  }
  return { months: data.months.filter(m => m.month <= toM), note: "toutes les années" };
}

function renderCostChart() {
  const b = costBounds(), auto = autoStep(b.days), step = costStep === "auto" ? auto : costStep;
  const sel = document.getElementById("costStep");
  sel.value = costStep; sel.options[0].text = `Auto · ${STEP_NAMES[auto]}`;
  const w = STEP_ORDER.indexOf(step) > STEP_ORDER.indexOf(auto) ? widen(step, b) : null;
  const charges = w?.charges ?? view.charges, months = w?.months ?? view.months, from = w?.from ?? b.from;
  document.getElementById("costTitle").innerHTML = `Coût par ${STEP_NAMES[step]}${w && !view.all ? ` <small>${w.note}</small>` : ""}`;
  const byDay = step === "day" || step === "week";
  const empty = byDay ? charges.length === 0 : months.length === 0;
  document.getElementById("monthsEmpty").hidden = !empty;
  document.getElementById("monthsChart").parentElement.hidden = empty;
  if (empty) { charts.monthsChart?.destroy(); delete charts.monthsChart; return; }
  const scales = { y: { beginAtZero: true, title: { display: true, text: "€" } },
                   y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false } } };
  if (byDay) {
    const bk = dayBuckets(step, from, b.to, charges), tot = bk.map(x => x.cost.reduce((a, v) => a + v, 0));
    let run = 0;
    const cumul = tot.map(v => run += v), dense = bk.length > 40;
    const datasets = TRANCHES.map((name, i) => ({ type: "bar", label: name, stack: "c", yAxisID: "y", order: 2,
      data: bk.map(x => x.cost[i]), kwh: bk.map(x => x.kwh[i]), hasData: bk.some(x => x.kwh[i] > 0),
      backgroundColor: isHP(i) ? hatch(css(TRANCHE_COLORS[i])) : css(TRANCHE_COLORS[i]),
      borderColor: css(TRANCHE_COLORS[i]), borderWidth: isHP(i) ? 1 : 0 }));
    datasets.push({ type: "line", label: "Cumul (€)", data: cumul, yAxisID: "y1", order: 1, hasData: true,
      borderColor: css("--accent"), backgroundColor: css("--accent"), tension: 0, pointRadius: dense ? 0 : 2 });
    const title = x => step === "day" ? dayLabel(x.key, { weekday: "short", day: "numeric", month: "short", year: "numeric" })
      : `Semaine du ${dayLabel(x.key, { day: "numeric", month: "short", year: "numeric" })}`;
    scales.x = { stacked: true, ticks: { maxRotation: 0, autoSkipPadding: 8 } }; scales.y.stacked = true; scales.y1.title = { display: true, text: "€ cumulés" };
    chart("monthsChart", {
      data: { labels: bk.map(x => dayLabel(x.key, { day: "numeric", month: "short" })), datasets },
      options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, scales,
        plugins: { legend: { labels: { boxWidth: 10, filter: (l, d) => d.datasets[l.datasetIndex].hasData } },
          tooltip: { filter: c => c.dataset.type === "line" || c.raw > 0, itemSort: (a, b) => a.datasetIndex - b.datasetIndex,
            callbacks: {
              title: items => title(bk[items[0].dataIndex]),
              label: c => c.dataset.type === "line" ? `Cumul depuis le ${dayLabel(from, { day: "numeric", month: "short" })} : ${eur(c.raw)}`
                : `${c.dataset.label} : ${eur(c.raw)} · ${fr(c.dataset.kwh[c.dataIndex], 1)} kWh`,
              footer: items => { const x = bk[items[0].dataIndex], t = tot[items[0].dataIndex];
                const part = step === "week" && (x.from !== x.full[0] || x.to !== x.full[1])
                  ? `\nSemaine partielle : du ${dayLabel(x.from, { day: "numeric", month: "short" })} au ${dayLabel(x.to, { day: "numeric", month: "short" })}` : "";
                return (t > 0 ? `Total : ${eur(t)}` : "Aucune recharge") + part; } } } } },
    });
    return;
  }
  const rows = step === "year" ? yearBuckets(months)
    : months.map(m => ({ label: new Date(m.month + "-01").toLocaleDateString("fr-FR", { month: "short", year: "2-digit" }),
                              cost: m.cost, eur_100km: m.eur_100km, note: "" }));
  scales.y1.title = { display: true, text: "€/100 km" };
  chart("monthsChart", {
    data: { labels: rows.map(m => m.label), datasets: [
      { type: "bar", label: "Coût (€)", data: rows.map(m => m.cost), backgroundColor: css("--accent"), borderRadius: 6, yAxisID: "y", order: 2 },
      { type: "line", label: "€/100 km", data: rows.map(m => m.eur_100km === "" ? null : m.eur_100km),
        borderColor: css("--rouge"), backgroundColor: css("--rouge"), tension: .3, yAxisID: "y1", order: 1 }] },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, scales,
      plugins: { legend: { labels: { boxWidth: 10 } },
                 tooltip: { callbacks: { label: c => `${c.dataset.label} : ${fr(c.raw, 2)}`,
                                         footer: items => rows[items[0].dataIndex].note } } } },
  });
}

function renderCharts() {
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--border");
  renderCostChart();

  const tr = view.tranches, total = tr.reduce((s, v) => s + v, 0);
  document.getElementById("tranchesEmpty").hidden = total > 0;
  document.getElementById("tranchesChart").parentElement.hidden = total === 0;
  if (total > 0) chart("tranchesChart", {
    type: "doughnut",
    data: { labels: TRANCHES, datasets: [{ data: tr, backgroundColor: TRANCHE_COLORS.map((v, i) => isHP(i) ? hatch(css(v)) : css(v)),
      borderColor: TRANCHE_COLORS.map(css), borderWidth: TRANCHES.map((_, i) => isHP(i) ? 1.5 : 0) }] },
    options: { maintainAspectRatio: false, cutout: "62%",
      plugins: { legend: { position: "bottom", labels: { boxWidth: 14, boxHeight: 14 } },
                 tooltip: { callbacks: { label: c => `${c.label} : ${fr(c.raw, 1)} kWh (${fr(100 * c.raw / total, 0)} %)` } } } },
  });
}

// ------------------------------------------------------------------ saison Tempo
function renderSeason() {
  const el = document.getElementById("season"), s = data.season || {};
  if (s.error || !s.stats) { el.innerHTML = `<div class="empty">API Tempo indisponible : ${esc(s.error || "pas de données")}</div>`; return; }
  document.getElementById("seasonName").textContent = s.periode;
  const st = s.stats, total = { BLEU: (st.joursBleusConsommes || 0) + (st.joursBleusRestants || 0),
    BLANC: (st.joursBlancsConsommes || 0) + (st.joursBlancsRestants || 0), ROUGE: (st.joursRougesConsommes || 0) + (st.joursRougesRestants || 0) };
  const left = { BLEU: st.joursBleusRestants, BLANC: st.joursBlancsRestants, ROUGE: st.joursRougesRestants };
  const colorOf = Object.fromEntries(s.days.filter(d => d.color).map(d => [d.day, d.color]));
  const today = iso(new Date()), tomorrow = iso(new Date(Date.now() + 864e5));
  const stock = ["BLANC", "ROUGE", "BLEU"].map(c => `<div><div class="label">${QUOTA[c]} restants</div>
      <div class="n">${left[c] ?? "–"}<small> / ${total[c]}</small></div>
      <div class="gauge"><span style="width:${total[c] ? 100 * (left[c] || 0) / total[c] : 0}%;background:var(--${c.toLowerCase()})"></span></div></div>`).join("");
  const start = new Date(+s.periode.slice(0, 4), 8, 1), months = [];
  for (let i = 0; i < 12; i++) {
    const first = new Date(start.getFullYear(), start.getMonth() + i, 1), n = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
    const cells = Array((first.getDay() + 6) % 7).fill('<div class="day pad"></div>');
    for (let d = 1; d <= n; d++) {
      const k = iso(new Date(first.getFullYear(), first.getMonth(), d)), c = colorOf[k] || "";
      cells.push(`<div class="day ${c}${k === today ? " today" : ""}" title="${dshort(k)}${c ? " : " + c : ""}"></div>`);
    }
    months.push(`<div class="month"><div class="title">${first.toLocaleDateString("fr-FR", { month: "short", year: "2-digit" })}</div>
      <div class="days">${cells.join("")}</div></div>`);
  }
  el.innerHTML = `<div class="stock">${stock}</div>
    <div class="tomorrow">Demain : ${colorOf[tomorrow] ? `<span class="pill ${colorOf[tomorrow]}">${colorOf[tomorrow]}</span>` : "pas encore publié"}
      · ${Math.max(0, Math.round((new Date(start.getFullYear() + 1, 8, 1) - new Date(today)) / 864e5))} jours avant la fin de saison</div>
    <details class="cal"${seasonOpen ? " open" : ""}><summary><span class="chev">▸</span> Calendrier de la saison</summary>
    <div class="months">${months.join("")}</div>
    <div class="legend"><span><i style="background:var(--bleu)"></i>Bleu</span><span><i style="background:var(--blanc)"></i>Blanc</span>
      <span><i style="background:var(--rouge)"></i>Rouge</span><span>Rouges du 1er nov. au 31 mars, hors week-end</span></div></details>`;
  el.querySelector("details.cal").addEventListener("toggle", e => { seasonOpen = e.target.open; });
}

// ------------------------------------------------------------------ courbes de charge
// Points relevés par la source de la marque ; sans courbe (recharge saisie à la main), droite début → fin tirée du Sheet
function curvePoints(r) {
  const pts = data.charge_curves?.curves?.[r[0]];
  if (pts?.length >= 2) return { real: true, pts: pts.map(([d, level, autonomy]) => ({ x: +new Date(d), y: level, autonomy })) };
  const start = +new Date(r[C.start]), h = num(r[C.duration]);
  if (!start || !h || r[3] === "" || r[4] === "") return null;
  return { real: false, pts: [{ x: start, y: num(r[3]) }, { x: start + h * 36e5, y: num(r[4]) }] };
}

function toggleCurve(tr) {
  const open = tr.getAttribute("aria-expanded") !== "true", row = tr.nextElementSibling, id = tr.dataset.id;
  tr.setAttribute("aria-expanded", open);
  row.hidden = !open || tr.hidden;
  open ? openCurves.add(id) : openCurves.delete(id);
  const key = "curve-" + id;
  if (!open) { charts[key]?.destroy(); delete charts[key]; row.cells[0].innerHTML = ""; return; }
  const r = view.charges.find(x => x[0] === id), c = r && curvePoints(r), cell = row.cells[0];
  cell.innerHTML = `<div class="curve" style="width:${document.getElementById("table").clientWidth}px">${c
    ? '<div class="plot"><canvas></canvas></div><div class="hint"></div>' : '<div class="empty">Pas de courbe pour cette recharge</div>'}</div>`;
  if (!c) return;
  drawCurve(key, cell.querySelector("canvas"), cell.querySelector(".hint"), r, c);
}

function drawCurve(key, canvas, hint, r, { real, pts }) {
  const hour = 36e5, span = pts.at(-1).x - pts[0].x, step = span > 12 * hour ? 2 * hour : hour;
  // Bornes sur des heures pleines (paires si pas de 2 h), graduations posées à la main pour rester alignées
  let xmin = Math.floor((pts[0].x - hour / 2) / hour) * hour;
  if (step > hour && new Date(xmin).getHours() % 2) xmin -= hour;
  const xmax = Math.ceil((pts.at(-1).x + hour / 2 - xmin) / step) * step + xmin;
  const ymin = Math.max(0, Math.floor((Math.min(...pts.map(p => p.y)) - 5) / 10) * 10);
  const hhmm = x => new Date(x).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const kw = (a, b) => data.battery_kwh ? (b.y - a.y) / 100 * data.battery_kwh / ((b.x - a.x) / hour) : null;
  const [hcS, hcE] = data.hc_hours.map(Number), hcColor = css("--bleu-hc");
  // Bandes heures creuses (fond), découpées sur les jours couverts par l'axe
  const hcBands = {
    id: "hcBands",
    beforeDatasetsDraw(ch) {
      const { ctx, chartArea: a, scales: { x } } = ch;
      ctx.save();
      ctx.fillStyle = hcColor; ctx.globalAlpha = .15;
      for (let d = new Date(xmin - 864e5); +d < xmax; d.setDate(d.getDate() + 1)) {
        const s = new Date(d.getFullYear(), d.getMonth(), d.getDate(), hcS);
        const e = new Date(d.getFullYear(), d.getMonth(), d.getDate() + (hcE <= hcS ? 1 : 0), hcE);
        const x0 = Math.max(x.getPixelForValue(+s), a.left), x1 = Math.min(x.getPixelForValue(+e), a.right);
        if (x1 > x0) ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
      }
      ctx.restore();
    },
  };
  charts[key] = new Chart(canvas, {
    type: "line",
    data: { datasets: [{ data: pts, borderColor: css("--accent"), backgroundColor: css("--accent"),
      pointRadius: real ? 4 : 0, pointHoverRadius: 6, borderWidth: 2,
      segment: { borderDash: () => [6, 4] } }] },
    options: { maintainAspectRatio: false, animation: false, parsing: false,
      interaction: { mode: "nearest", intersect: false },
      scales: { x: { type: "linear", min: xmin, max: xmax, ticks: { callback: v => new Date(v).getHours() + "h" },
                   afterBuildTicks: ax => { ax.ticks = []; for (let t = xmin; t <= xmax; t += step) ax.ticks.push({ value: t }); } },
                y: { min: ymin, max: 100, ticks: { callback: v => v + " %" } } },
      plugins: { legend: { display: false },
        tooltip: { callbacks: {
          title: items => new Date(items[0].raw.x).toLocaleString("fr-FR", { weekday: "short", hour: "2-digit", minute: "2-digit" }),
          label: c => {
            const p = c.raw, prev = pts[c.dataIndex - 1], rate = prev && kw(prev, p);
            return [`${fr(p.y, 0)} %${p.autonomy ? ` · ${fr(p.autonomy, 0)} km d'autonomie` : ""}`,
                    ...(rate != null ? [`≈ ${fr(rate, 2)} kW depuis ${hhmm(prev.x)}`] : [])];
          } } } } },
    plugins: [hcBands],
  });
  const h = num(r[C.duration]), avg = h ? num(r[C.kwhBat]) / h : 0;
  hint.textContent = (real ? `${pts.length} points relevés ; entre deux points la courbe est interpolée (pointillés).`
    : "Recharge sans relevé : droite estimée entre le début et la fin.")
    + (avg ? ` Puissance moyenne ≈ ${fr(avg, 2)} kW.` : "") + ` Fond bleu : heures creuses ${hcS}h–${hcE}h.`;
}

// ------------------------------------------------------------------ tableaux
// Lignes regroupées par mois (du plus récent au plus ancien)
function byMonth(rows, col) {
  const months = new Map();
  rows.forEach(r => { const m = String(r[col]).slice(0, 7); months.has(m) || months.set(m, []); months.get(m).push(r); });
  return [...months];
}

const monthLabel = m => new Date(m + "-01T12:00").toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
const monthHead = (m, title) => `<tr class="fold month-row" data-month="${m}" aria-expanded="false" title="${title}">
  <td><span class="chev">▸</span> ${monthLabel(m)}</td>`;

// Barre de répartition HC/HP à partir des kWh des 6 tranches
function slotBar(parts) {
  const total = parts.reduce((a, b) => a + (b || 0), 0) || 1;
  return `<div class="bar">${parts.map((p, i) => p > 0 ? `<span style="width:${100 * p / total}%;background:${trancheFill(i)}" title="${TRANCHES[i]} : ${fr(p, 2)} kWh"></span>` : "").join("")}</div>`;
}

// Carburants de référence (PETROL_FUELS) dont la consommation est connue ; couleur et libellés par carburant
const fuels = () => (data.offers?.fuels || []).filter(f => num(f.l100));
const fuelClass = f => f.key === "gazole" ? "petrol diesel" : "petrol";
const fuelIn = f => f.key === "gazole" ? "GO" : f.name;                  // « ≈ 2,80 € en GO », « en SP98 »
const fuelLabel = f => ({ gazole: "Diesel GO", gplc: "GPL", e85: "Superéthanol E85" })[f.key] || `Essence ${f.name}`;

// Équivalent carburant d'une recharge : km de la ligne (compteur) s'ils sont connus, sinon km que permet l'énergie
// chargée (kWh batterie ÷ consommation moyenne des trajets) ; × L/100 km du carburant × son prix moyen le jour de la
// recharge (prix actuel à défaut)
function fuelEq(r, f) {
  const trips = data.trips.rows, cur = f.current && !f.current.error ? f.current : null;
  const km = sum(trips.filter(t => num(t[T.kwh]) > 0), T.km), kwh100 = km ? 100 * sum(trips, T.kwh) / km : 0;
  const kwhBat = num(r[C.kwhBat]), day = String(r[C.start]).slice(0, 10), hist = (data.fuel_history?.[f.key] || {})[day];
  const price = hist?.price ?? cur?.price ?? 0, l100 = num(f.l100);
  const rowKm = num(r[C.km]), eqKm = rowKm > 0 ? rowKm : kwh100 && kwhBat ? 100 * kwhBat / kwh100 : 0;
  if (!l100 || !eqKm || !price) return null;
  return { fuel: f, cost: eqKm * l100 / 100 * price, km: eqKm, price, l100,
           kmHow: rowKm > 0 ? `${fr(eqKm, 0)} km parcourus` : `≈ ${fr(eqKm, 0)} km estimés (${fr(kwh100, 1)} kWh/100 km)`,
           when: hist ? `du ${new Date(day).toLocaleDateString("fr-FR")}` : "actuel" };
}
const fuelEqs = r => fuels().map(f => fuelEq(r, f)).filter(Boolean);
const fuelTag = eq => `<small class="${fuelClass(eq.fuel)}" title="${esc(`${eq.kmHow} × ` +
  `${fr(eq.l100, 1)} L/100 km × ${fr(eq.price, 3)} €/L (prix moyen ${fuelIn(eq.fuel)} ${eq.when})`)}">≈ ${eur(eq.cost)} en ${esc(fuelIn(eq.fuel))}</small>`;
const fuelCells = r => fuelEqs(r).map(fuelTag).join("");
// Total du mois par carburant
const fuelMonth = list => fuels().map(f => {
  const eqs = list.map(r => fuelEq(r, f)).filter(Boolean);
  return eqs.length ? `<small class="${fuelClass(f)}" title="Total des équivalents ${esc(fuelIn(f))} du mois">≈ ${
    eur(eqs.reduce((t, e) => t + e.cost, 0))} en ${esc(fuelIn(f))}</small>` : "";
}).join("");

// Dépliage des mois : les lignes data-in du mois, et pour les recharges la courbe si elle était ouverte
function bindMonths(el, open) {
  el.querySelectorAll("tr.month-row").forEach(tr => {
    const toggle = show => {
      tr.setAttribute("aria-expanded", show);
      el.querySelectorAll(`tr[data-in="${tr.dataset.month}"]`).forEach(x => {
        x.hidden = !show;
        if (x.nextElementSibling?.classList.contains("curve-row"))
          x.nextElementSibling.hidden = !show || x.getAttribute("aria-expanded") !== "true";
      });
      show ? open.add(tr.dataset.month) : open.delete(tr.dataset.month);
    };
    tr.addEventListener("click", () => toggle(tr.getAttribute("aria-expanded") !== "true"));
    if (open.has(tr.dataset.month)) toggle(true);
  });
}

function renderTable() {
  const el = document.getElementById("table");
  Object.keys(charts).filter(k => k.startsWith("curve-")).forEach(k => { charts[k].destroy(); delete charts[k]; });
  if (currentTab === "charges") {
    const rows = [...view.charges].reverse();
    if (!rows.length) { el.innerHTML = '<div class="empty">Aucune recharge sur la période</div>'; return; }
    el.innerHTML = `<table><thead><tr><th>Début</th><th>Durée</th><th>% batterie</th><th>kWh batterie</th>
      <th>Couleur</th><th>Répartition HC/HP</th><th>kWh compteur</th><th>Coût</th>
      <th>€/kWh</th><th>Km</th><th>€/100 km</th><th>Note</th></tr></thead><tbody>${
      byMonth(rows, C.start).map(([m, list]) => {
        const cost = sum(list, C.cost), kwh = sum(list, C.kwh), km = sum(list, C.km);
        const eur100 = ratio(sum(list.filter(r => num(r[C.km]) > 0), C.cost), km, 100), eurKwh = ratio(cost, kwh);
        const slots = TRANCHES.map((_, i) => sum(list, C.slots + i));
        return monthHead(m, "Afficher les recharges du mois") + `<td><small>${list.length} recharge${list.length > 1 ? "s" : ""}</small></td><td></td>
          <td>${fr(sum(list, C.kwhBat), 1)}</td><td></td><td class="text">${slotBar(slots)}</td><td>${fr(kwh, 1)}</td><td>${eur(cost)}${fuelMonth(list)}</td>
          <td>${eurKwh === "" ? "–" : eur(eurKwh, 4)}</td><td>${fr(km, 0)}</td><td>${eur100 === "" ? "–" : eur(eur100)}</td><td></td></tr>` +
        list.map(r => `<tr class="fold" data-in="${m}" data-id="${esc(r[0])}" aria-expanded="false" title="Afficher la courbe de charge" hidden>
          <td><span class="chev">▸</span> ${dt(r[C.start])}</td><td>${fr(r[C.duration], 1)} h</td><td>${fr(r[3], 0)} → ${fr(r[4], 0)} %</td>
          <td>${fr(r[C.kwhBat], 1)}${data.battery_kwh && num(r[C.kwhBat]) ? ` <small>(${fr(100 * num(r[C.kwhBat]) / data.battery_kwh, 0)} %)</small>` : ""}</td><td class="text">${r[C.color] ? `<span class="pill ${esc(r[C.color])}">${esc(r[C.color])}</span>` : "–"}</td>
          <td class="text">${slotBar(r.slice(C.slots, C.slots + 6).map(Number))}</td><td>${fr(r[C.kwh], 1)}</td><td>${eur(r[C.cost])}${fuelCells(r)}</td>
          <td>${eur(r[C.eurKwh], 4)}</td><td>${fr(r[C.km], 0)}</td><td>${eur(r[C.eur100])}</td><td class="note">${esc(r[C.note])}</td></tr>
          <tr class="curve-row" hidden><td colspan="12"></td></tr>`).join("");
      }).join("")}</tbody></table>`;
    bindMonths(el, openMonths.charges);
    el.querySelectorAll("tr.fold[data-id]").forEach(tr => {
      tr.addEventListener("click", () => toggleCurve(tr));
      if (openCurves.has(tr.dataset.id)) toggleCurve(tr);
    });
  } else {
    const rows = [...view.trips].reverse();
    if (!rows.length) { el.innerHTML = '<div class="empty">Aucun trajet sur la période</div>'; return; }
    el.innerHTML = `<table><thead><tr><th>Début</th><th>Fin</th><th>Distance</th><th>Conso batterie</th>
      <th>kWh/100 km</th><th>Compteur fin</th></tr></thead><tbody>${
      byMonth(rows, T.start).map(([m, list]) => {
        const kwh100 = ratio(sum(list, T.kwh), sum(list.filter(r => num(r[T.kwh]) > 0), T.km), 100);
        const odo = Math.max(...list.map(r => num(r[T.odo])));
        return monthHead(m, "Afficher les trajets du mois") + `<td><small>${list.length} trajet${list.length > 1 ? "s" : ""}</small></td><td>${fr(sum(list, T.km), 1)} km</td>
          <td>${fr(sum(list, T.kwh), 2)} kWh</td><td>${kwh100 === "" ? "–" : fr(kwh100, 1)}</td><td>${odo ? fr(odo, 0) + " km" : "–"}</td></tr>` +
          list.map(r => `<tr data-in="${m}" hidden><td>${dt(r[T.start])}</td><td>${dt(r[T.end])}</td>
            <td>${fr(r[T.km], 1)} km</td><td>${fr(r[T.kwh], 2)} kWh</td><td>${fr(r[T.kwh100], 1)}</td><td>${fr(r[T.odo], 0)} km</td></tr>`).join("");
      }).join("")}</tbody></table>`;
    bindMonths(el, openMonths.trips);
  }
}

// ------------------------------------------------------------------ chargement
function render() {
  view = computeView();
  renderPeriod(); renderKpis(); renderOffers(); renderCharts(); renderTable();
}

async function load(force = false) {
  const err = document.getElementById("error");
  try {
    const resp = await fetch("api/data" + (force ? "?refresh=1" : ""));
    if (resp.status === 401) { location.href = "login"; return; }  // session expirée
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.error || resp.status);
    data = body;
    err.style.display = "none";
    document.getElementById("fetched").textContent = "Données du Sheet au " + new Date(data.fetched_at).toLocaleString("fr-FR");
    document.getElementById("logout").hidden = !data.auth;
    renderTitle(); renderHealth(); renderCar(); renderLastCharge(); renderSeason(); renderFooter(); render();
  } catch (e) {
    err.textContent = "Impossible de lire le Google Sheet : " + e.message;
    err.style.display = "block";
  }
}

const preset = document.getElementById("preset");
preset.value = (store.get("ev.preset") ?? store.get("e208.preset")) || "month";  // par défaut : mois en cours
document.getElementById("from").value = (store.get("ev.from") ?? store.get("e208.from")) || "";
document.getElementById("to").value = (store.get("ev.to") ?? store.get("e208.to")) || "";
document.getElementById("customDates").hidden = preset.value !== "custom";
function onPeriod() {
  document.getElementById("customDates").hidden = preset.value !== "custom";
  store.set("ev.preset", preset.value);
  store.set("ev.from", document.getElementById("from").value);
  store.set("ev.to", document.getElementById("to").value);
  if (data) render();
}
[preset, document.getElementById("from"), document.getElementById("to")].forEach(e => e.addEventListener("change", onPeriod));
document.getElementById("costStep").addEventListener("change", e => { costStep = e.target.value; if (data) renderCostChart(); });
document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("active", x === b));
  currentTab = b.dataset.tab;
  if (data) renderTable();
}));
document.getElementById("refresh").addEventListener("click", () => load(true));
document.getElementById("logout").addEventListener("click", () => { location.href = "/logout"; });
const systemDark = matchMedia("(prefers-color-scheme: dark)");
const isDark = () => document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : systemDark.matches;
function renderThemeButton() {
  const b = document.getElementById("theme"), dark = isDark();
  b.textContent = dark ? "☀️" : "🌙";
  b.title = b.ariaLabel = dark ? "Passer en thème clair" : "Passer en thème sombre";
}
document.getElementById("theme").addEventListener("click", () => {
  const theme = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch (e) {}
  renderThemeButton();
  applyThemeColor();
  if (data) { renderCharts(); renderTable(); }
});
systemDark.addEventListener("change", () => { renderThemeButton(); if (data) { renderCharts(); renderTable(); } });
renderThemeButton();
load();
setInterval(load, 5 * 60 * 1000);
// estimation de la charge en cours : recalculée chaque minute, sans requête
setInterval(() => { if (data?.charge_estimate) renderCar(); }, 60 * 1000);
