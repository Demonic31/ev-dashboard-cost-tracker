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
const TRANCHE_COLORS = ["--bleu-hc", "--bleu", "--blanc-hc", "--blanc", "--rouge-hc", "--rouge"];
const QUOTA = { BLEU: "Bleus", BLANC: "Blancs", ROUGE: "Rouges" };
let data = null, charts = {}, currentTab = "charges", view = null;
const openCurves = new Set();  // recharges dépliées, conservées d'un rendu à l'autre
const openMonths = { charges: new Set(), trips: new Set() };  // mois dépliés, par tableau
let seasonOpen = false;  // calendrier Tempo déplié

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
      <div class="sub">${fr(r[C.eurKwh], 4)} €/kWh</div>${eqs.length ? `<div class="sub fuels">${eqs.map(fuelTag).join("")}</div>` : ""}</div>
    <div class="slots"><div class="label">Heures creuses / pleines</div>${slotBar(r.slice(C.slots, C.slots + 6).map(Number))}</div>`;
}
function renderLastCharge() {
  const el = document.getElementById("lastCharge"), r = lastCharge();
  el.hidden = !r;
  if (!r) return;
  const when = new Date(r[C.start]).toLocaleString("fr-FR", { weekday: "short", day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit" });
  el.innerHTML = `<button type="button" class="lc-summary" aria-expanded="${lastChargeOpen}"><span class="chev">▸</span>
    <b>🔌 Dernière recharge</b> <span>${when}</span><span>${hDuration(num(r[C.duration]))}</span>
    <span>${fr(r[3], 0)} → ${fr(r[4], 0)} %</span><span>${fr(r[C.kwhBat], 1)} kWh</span><b class="cost">${eur(r[C.cost])}</b></button>
    <div class="items"${lastChargeOpen ? "" : " hidden"}>${lastChargeItems(r)}</div>`;
  el.querySelector(".lc-summary").addEventListener("click", () => { lastChargeOpen = !lastChargeOpen; renderLastCharge(); });
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

// Part de la durée d'une charge en prix « bas » Zen Week-End + HC : week-end, jour férié ou heures creuses en semaine
function zenLowShare(r) {
  const start = new Date(r[C.start]), end = new Date(r[2]);
  if (!(end > start)) return null;
  const [hcStart, hcEnd] = data.hc_hours.map(Number), step = 5 * 60e3;
  let low = 0, n = 0;
  for (let t = start.getTime() + step / 2; t < end.getTime(); t += step, n++) {
    const d = new Date(t), hour = d.getHours() + d.getMinutes() / 60;
    const hc = hcStart > hcEnd ? hour >= hcStart || hour < hcEnd : hour >= hcStart && hour < hcEnd;
    if (hc || d.getDay() === 0 || d.getDay() === 6 || isHoliday(d)) low++;
  }
  return n ? low / n : null;
}

function renderOffers() {
  const o = data.offers || {}, coef = num(data.loss_coef || 1), rows = view.charges;
  const hcKwh = rows.reduce((s, r) => s + num(r[C.slots]) + num(r[C.slots + 2]) + num(r[C.slots + 4]), 0) * coef;
  const hpKwh = rows.reduce((s, r) => s + num(r[C.slots + 1]) + num(r[C.slots + 3]) + num(r[C.slots + 5]), 0) * coef;
  // km de l'équivalent essence : trajets enregistrés ou compteur entre recharges, le plus grand des deux
  const odoKm = num(view.kpi[2]), tripKm = num(view.kpi[7]), km = Math.max(odoKm, tripKm), [hcS, hcE] = data.hc_hours;
  const kmSource = !km ? "aucun trajet ni relevé compteur" : tripKm >= odoKm ? "trajets enregistrés" : "compteur entre recharges";
  const hcLabel = `HC ${hcS}h–${hcE}h`;
  const list = [{ name: "Tempo", sub: "ton contrat (Sheet)", cost: view.cost, color: "--accent", ref: true }];
  list.push(o.base ? { name: "Base", sub: `${fr(o.base, 4)} €/kWh`, cost: view.kwh * o.base, color: "--base" }
                   : { name: "Base", missing: "BASE_PRICE_KWH" });
  list.push(o.hchp?.hc && o.hchp?.hp
    ? { name: "Heures creuses", sub: `${hcLabel} ${fr(o.hchp.hc, 4)} · HP ${fr(o.hchp.hp, 4)} €/kWh`,
        cost: hcKwh * o.hchp.hc + hpKwh * o.hchp.hp, color: "--bleu" }
    : { name: "Heures creuses", missing: "HCHP_HC_PRICE_KWH / HCHP_HP_PRICE_KWH" });
  if (o.zen?.low && o.zen?.high) {
    let cost = 0, lowKwh = 0;
    rows.forEach(r => { const share = zenLowShare(r) ?? 1, kwh = num(r[C.kwh]);
      lowKwh += kwh * share; cost += kwh * (share * o.zen.low + (1 - share) * o.zen.high); });
    list.push({ name: "Zen Week-End + HC", sub: `WE, fériés et ${hcLabel} ${fr(o.zen.low, 4)} · semaine HP ${fr(o.zen.high, 4)} €/kWh` +
      (view.kwh ? ` · ${fr(100 * lowKwh / view.kwh, 0)} % au prix bas` : ""), cost, color: "--blanc-hc" });
  } else list.push({ name: "Zen Week-End + HC", missing: "ZEN_LOW_PRICE_KWH / ZEN_HIGH_PRICE_KWH" });
  (o.fuels?.length ? o.fuels : [{ key: "sp98", name: "SP98" }]).forEach(f => {
    const cur = f.current, color = f.key === "gazole" ? "--diesel" : "--petrol";
    if (num(f.l100) && cur && !cur.error)
      list.push({ name: fuelLabel(f), sub: `${fr(f.l100, 1)} L/100 km × ${fr(cur.price, 3)} €/L (moyenne ${cur.zone}, ` +
        `${cur.stations} stations, ${cur.days} derniers jours, relevé le ${new Date(cur.fetched_at).toLocaleDateString("fr-FR")})`,
        cost: km * f.l100 / 100 * cur.price, color });
    else list.push({ name: fuelLabel(f), missing: cur?.error ? "prix carburant indisponible : " + cur.error
      : `la consommation dans PETROL_FUELS (ex. ${f.key}:${f.key === "gazole" ? "5.5" : "7"})`, raw: !!cur?.error });
  });

  const max = Math.max(...list.map(x => x.cost || 0)) || 1, ref = view.cost;
  document.getElementById("offers").innerHTML = list.map(x => {
    if (x.missing) return `<div class="offer"><div class="name">${x.name}</div><div class="missing">${x.raw ? esc(x.missing) : `Renseigner ${esc(x.missing)} dans .env`}</div></div>`;
    const d = x.cost - ref, pct = ref ? 100 * d / ref : 0;
    return `<div class="offer${x.ref ? " ref" : ""}"><div class="name">${x.name}<small>${esc(x.sub)}</small></div>
      <div class="track"><span style="width:${100 * x.cost / max}%;background:${css(x.color)}"></span></div>
      <div class="cost">${eur(x.cost)}</div>
      <div class="delta ${x.ref ? "" : d > 0.005 ? "up" : d < -0.005 ? "down" : ""}">${x.ref ? (view.kpi[3] !== "" ? eur(view.kpi[3]) + " /100 km" : "")
        : `${d >= 0 ? "+" : "−"}${eur(Math.abs(d))} (${d >= 0 ? "+" : "−"}${fr(Math.abs(pct), 0)} %)`}</div></div>`;
  }).join("") + `<ul class="notes">
    <li>Coût de l'énergie seule (kWh facturés = kWh batterie × ${fr(coef, 2)}), abonnements non compris.</li>
    <li>Heures creuses et Zen supposent les mêmes heures creuses que Tempo (${hcLabel}, onglet Paramètres) ; Zen est réparti au prorata du temps de charge.</li>
    <li>Carburants : ${fr(km, 0)} km parcourus sur la période (${kmSource}) ; prix moyen du carburant relu chaque jour.</li></ul>`;
}

// ------------------------------------------------------------------ graphiques
function chart(id, config) {
  charts[id]?.destroy();
  charts[id] = new Chart(document.getElementById(id), config);
}

function renderCharts() {
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--border");
  const months = view.months;
  document.getElementById("monthsEmpty").hidden = months.length > 0;
  document.getElementById("monthsChart").parentElement.hidden = months.length === 0;
  const datasets = [
    { type: "bar", label: "Coût (€)", data: months.map(m => m.cost), backgroundColor: css("--accent"),
      borderRadius: 6, yAxisID: "y", order: 2 },
  ];
  datasets.push({ type: "line", label: "€/100 km", data: months.map(m => m.eur_100km === "" ? null : m.eur_100km),
    borderColor: css("--rouge"), backgroundColor: css("--rouge"), tension: .3, yAxisID: "y1", order: 1 });
  if (months.length) chart("monthsChart", {
    data: { labels: months.map(m => new Date(m.month + "-01").toLocaleDateString("fr-FR", { month: "short", year: "2-digit" })), datasets },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      scales: { y: { beginAtZero: true, title: { display: true, text: "€" } },
                y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false },
                      title: { display: true, text: "€/100 km" } } },
      plugins: { legend: { labels: { boxWidth: 10 } },
                 tooltip: { callbacks: { label: c => `${c.dataset.label} : ${fr(c.raw, 2)}` } } } },
  });

  const tr = view.tranches, total = tr.reduce((s, v) => s + v, 0);
  document.getElementById("tranchesEmpty").hidden = total > 0;
  document.getElementById("tranchesChart").parentElement.hidden = total === 0;
  if (total > 0) chart("tranchesChart", {
    type: "doughnut",
    data: { labels: TRANCHES, datasets: [{ data: tr, backgroundColor: TRANCHE_COLORS.map(css), borderWidth: 0 }] },
    options: { maintainAspectRatio: false, cutout: "62%",
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10 } },
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
  return `<div class="bar">${parts.map((p, i) => p > 0 ? `<span style="width:${100 * p / total}%;background:${css(TRANCHE_COLORS[i])}" title="${TRANCHES[i]} : ${fr(p, 2)} kWh"></span>` : "").join("")}</div>`;
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
    renderTitle(); renderHealth(); renderCar(); renderLastCharge(); renderSeason(); render();
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
