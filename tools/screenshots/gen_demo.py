"""Jeu de données fictif pour les captures du README, au format de /api/data.

Usage : python3 gen_demo.py <sortie.json>   (lancé par run.sh)
Dates figées autour du 5 novembre 2026 : shots.js règle l'horloge du navigateur sur cette date.
"""
import json, os, random, sys
from datetime import datetime, timedelta, date

random.seed(208)
BAT, LOSS = 46.0, 1.15
PRICE = {("BLEU", "HC"): 0.1288, ("BLEU", "HP"): 0.1552, ("BLANC", "HC"): 0.1447,
         ("BLANC", "HP"): 0.1792, ("ROUGE", "HC"): 0.1518, ("ROUGE", "HP"): 0.6586}
SLOTS = [("BLEU", "HC"), ("BLEU", "HP"), ("BLANC", "HC"), ("BLANC", "HP"), ("ROUGE", "HC"), ("ROUGE", "HP")]
start_day, end_day = date(2026, 9, 1), date(2026, 11, 4)

colors = {}
d = start_day
while d <= date(2026, 11, 6):
    colors[d] = "BLANC" if d.weekday() < 5 and random.random() < 0.15 and d.month >= 10 else "BLEU"
    d += timedelta(days=1)
colors[date(2026, 11, 3)] = "ROUGE"
colors[date(2026, 11, 5)] = "BLANC"

iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S")
utc = lambda t: (t - timedelta(hours=1 if t >= datetime(2026, 10, 25, 3) else 2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
trips, charges, curves, rates = [], [], {}, []
odo, level, last_charge_odo = 41250.0, 88.0, 41250.0

def add_trips(d, n, t):
    global odo, level
    for _ in range(n):
        km = round(random.choice([random.uniform(2, 9), random.uniform(10, 32), random.uniform(35, 70)]), 1)
        mins = int(km * random.uniform(1.2, 2.0)) + 3
        kwh100 = round(random.uniform(13.5, 19.5), 1)
        kwh = round(km * kwh100 / 100, 2)
        odo += km; level -= 100 * kwh / BAT
        end = t + timedelta(minutes=mins)
        trips.append(["T-" + utc(t)[:19].replace("-", "").replace(":", "") + "Z", iso(t), iso(end), km, kwh, kwh100, round(odo, 1), iso(t)[:7]])
        t = end + timedelta(hours=random.uniform(1, 4))

d = start_day
while d <= end_day:
    n = random.choice([0, 1, 2, 2, 3, 4]) if d.weekday() < 5 else random.choice([0, 0, 1, 2])
    if d == end_day: n = 2
    add_trips(d, n, datetime.combine(d, datetime.min.time()) + timedelta(hours=random.choice([7, 8, 9]), minutes=random.randint(0, 59)))
    last = d == end_day
    if (level < random.uniform(60, 75) and d < end_day - timedelta(days=2)) or last:
        s = datetime.combine(d, datetime.min.time()) + timedelta(hours=22, seconds=random.randint(20, 90))
        if last: level = 31.0
        sl, el = round(level), 100
        kwh = round(BAT * (el - sl) / 100, 2)
        rate = 3.3 if last else random.uniform(2.2, 2.5)  # dernière : déborde en heures pleines
        dur = kwh / rate
        stop = s + timedelta(hours=dur)
        hc_end = datetime.combine(d + timedelta(days=1), datetime.min.time()) + timedelta(hours=6)
        hp_part = max(0, (stop - hc_end).total_seconds() / 3600) * rate
        hc_part = kwh - hp_part
        col_hc, col_hp = colors[d], colors[d + timedelta(days=1)]
        slots = [0.0] * 6
        slots[SLOTS.index((col_hc, "HC"))] += round(hc_part, 3)
        if hp_part: slots[SLOTS.index((col_hp, "HP"))] += round(hp_part, 3)
        billed = kwh * LOSS
        cost = sum(slots[i] * LOSS * PRICE[SLOTS[i]] for i in range(6))
        km = round(odo - last_charge_odo, 1)
        cid = "C-" + utc(s)[:19].replace("-", "").replace(":", "") + "Z"
        charges.append([cid, iso(s), iso(stop), sl, el, kwh, round(odo, 1), *slots, "psacc", "", round(dur, 2),
                        col_hc, round(billed, 3), round(cost, 4), round(cost / billed, 4), km, round(100 * cost / km, 4) if km else "", iso(s)[:7]])
        pts = []
        for off in [0, 0.75, 5 if dur > 5.5 else None, 7.9 if dur > 8.2 else None]:
            if off is None: continue
            lv = min(100, sl + 100 * rate * off / BAT)
            pts.append([utc(s + timedelta(hours=off)), round(lv), round(2.9 * lv)])
        pts.append([utc(stop), 100, None])
        curves[cid] = pts
        rates.append(100 * rate / BAT)
        level, last_charge_odo = 100.0, odo
    d += timedelta(days=1)

# trajets du 5 novembre, puis charge en cours le soir
add_trips(date(2026, 11, 5), 3, datetime(2026, 11, 5, 8, 12))

# une recharge saisie à la main, avec une note
charges[-4][13], charges[-4][14] = "manuel (estimation)", "Borne du parking, ticket perdu"
charges[-4][0] = "M" + charges[-4][0][1:]

def agg(rows):
    return sum(r[18] for r in rows), sum(r[17] for r in rows), sum(r[20] for r in rows)
months = []
for m in sorted({r[22] for r in charges}):
    rows = [r for r in charges if r[22] == m]; c, k, km = agg(rows)
    months.append({"month": m, "charges": len(rows), "kwh": k, "cost": c, "km": km, "eur_100km": 100 * c / km})
c, k, km = agg(charges)
tkm = sum(r[3] for r in trips); tkwh = sum(r[4] for r in trips)
labels = ['Coût total (€)', 'kWh facturés', 'Km parcourus', 'Coût / 100 km (€)', 'kWh / 100 km (compteur)',
          'Prix moyen (€/kWh)', 'Nb recharges', 'Km (trajets enregistrés)', 'kWh / 100 km (voiture)']
vals = [c, k, km, 100 * c / km, 100 * k / km, c / k, len(charges), tkm, 100 * tkwh / tkm]
tr_kwh = [sum(r[7 + i] for r in charges) * LOSS for i in range(6)]
season_days = []
d = date(2026, 9, 1)
while d <= date(2026, 11, 7):
    season_days.append({"day": d.isoformat(), "color": colors.get(d)}); d += timedelta(days=1)
used = {c_: sum(1 for x in season_days if x["color"] == c_ and x["day"] <= "2026-11-05") for c_ in ("BLEU", "BLANC", "ROUGE")}
now = "2026-11-05T23:05:00+01:00"
fuel_hist = {"sp98": {}, "gazole": {}}
d = start_day
while d <= date(2026, 11, 5):
    k_ = (d - start_day).days
    fuel_hist["sp98"][d.isoformat()] = {"price": round(1.86 + 0.03 * random.random() + k_ * 0.0006, 4), "stations": random.randint(3000, 5000)}
    fuel_hist["gazole"][d.isoformat()] = {"price": round(1.72 + 0.03 * random.random() + k_ * 0.0008, 4), "stations": random.randint(5000, 7500)}
    d += timedelta(days=1)
cur = lambda f, n: {"fuel": n, **fuel_hist[f]["2026-11-05"], "days": 3, "zone": "France", "fetched_at": now}
start_now = "2026-11-05T21:00:40+00:00"
med = sorted(rates[-4:])[1:3]; med = sum(med) / 2
data = {
    "kpi": [{"label": l, "value": v} for l, v in zip(labels, vals)],
    "months": months,
    "tranches": [{"tranche": f"{a.capitalize()} {b}", "kwh": v, "part": v / sum(tr_kwh)} for (a, b), v in zip(SLOTS, tr_kwh)],
    "charges": {"headers": [], "rows": charges},
    "trips": {"headers": [], "rows": trips},
    "colors": [{"day": d.isoformat(), "color": c_} for d, c_ in colors.items()],
    "hc_hours": [22, 6], "loss_coef": LOSS, "fetched_at": now,
    "tempo_prices": [{"since": "2026-08-01", **{c.lower(): [PRICE[(c, "HC")], PRICE[(c, "HP")]] for c in ("BLEU", "BLANC", "ROUGE")}}],
    "season": {"periode": "2026-2027", "stats": {"periode": "2026-2027", "bissextile": False, "dernierJourInclus": "2026-11-05",
               "joursBleusConsommes": used["BLEU"], "joursBlancsConsommes": used["BLANC"], "joursRougesConsommes": used["ROUGE"],
               "joursBleusRestants": 300 - used["BLEU"], "joursBlancsRestants": 43 - used["BLANC"],
               "joursRougesRestants": 22 - used["ROUGE"]}, "fetched_at": now, "days": season_days},
    "battery_kwh": BAT,
    "vehicle": {"name": "Peugeot e-208"},
    "health": {"soh": 97.0, "fetched_at": now, "note": "indicateur Stellantis (résistance interne) relevé par psacc"},
    "car": {"level": round(level), "autonomy": round(2.9 * level), "updated_at": "2026-11-05 21:00:40+00:00", "status": "charging",
            "plugged": True, "charging_rate": 11, "remaining_hours": 4.5, "mileage": round(odo, 1),
            "mileage_at": "2026-11-05 20:41:12+00:00"},
    "charge_curves": {"curves": curves, "ongoing": {"start_at": start_now, "points": [[start_now, round(level)]]},
                      "median_rate": med, "median_count": 4},
    "charge_estimate": {"from_at": start_now, "from_level": round(level), "from_autonomy": round(2.9 * level), "rate": round(med, 3),
                        "source": "médiane des 4 dernières recharges", "target": 100, "car_end_at": "2026-11-06T01:30:40+00:00"},
    "offers": {"base": 0.1985, "hchp": {"hc": 0.1589, "hp": 0.2142}, "zen": {"low": 0.1692, "high": 0.226},
               "fuels": [{"key": "sp98", "name": "SP98", "l100": 7.0, "current": cur("sp98", "SP98")},
                         {"key": "gazole", "name": "Gazole", "l100": 5.5, "current": cur("gazole", "GAZOLE")}]},
    "fuel_history": fuel_hist,
    "app": {"version": open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "web", "VERSION")).read().strip(), "label": ""},
    "credit": {"group": "Stellantis", "name": "psa_car_controller", "url": "https://github.com/flobz/psa_car_controller",
               "provides": "état de la voiture, santé de la batterie (SOH), courbes de charge, recharges et trajets"},
    "auth": False,
}
json.dump(data, open(sys.argv[1], "w"), ensure_ascii=False)
lc = charges[-1]
print(len(charges), "charges,", len(trips), "trajets, km", round(km), "coût", round(c, 2), "€ ; niveau", round(level),
      "; dernière", lc[1], lc[3], "→", lc[2], [round(x, 2) for x in lc[7:13]])
