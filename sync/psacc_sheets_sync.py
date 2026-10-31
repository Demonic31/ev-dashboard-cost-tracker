#!/usr/bin/env python3
"""Synchronise les recharges et trajets enregistrés par psa_car_controller vers le Google Sheet de suivi Tempo.

Lit une copie de la base SQLite de psacc (dates en UTC), convertit en heure de Paris, répartit l'énergie de
chaque recharge par couleur / tranche Tempo puis met à jour les onglets « Couleurs Tempo » (A:B),
« Recharges » (A:N) et « Trajets » (A:G). Ne touche jamais à la ligne 1 ni aux autres colonnes.
"""
import argparse
import bisect
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc
HC_START, HC_END = 22, 6  # heures creuses 22h -> 6h, jour Tempo 6h -> 6h
COLORS = {1: "BLEU", 2: "BLANC", 3: "ROUGE"}
SLOTS = [("BLEU", "HC"), ("BLEU", "HP"), ("BLANC", "HC"), ("BLANC", "HP"), ("ROUGE", "HC"), ("ROUGE", "HP")]
SHEET_EPOCH = datetime(1899, 12, 30)
DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Règles de découpage des trajets reprises de psa_car_controller (psacc/repository/trips.py)
TRIP_MAX_GAP_H = 2
TRIP_MAX_SPEED = 150

TAB_COLORS = "Couleurs Tempo"
TAB_CHARGES = "Recharges"
TAB_TRIPS = "Trajets"

log = logging.getLogger("psacc-sheets-sync")


# --------------------------------------------------------------------------- dates

def parse_ts(value):
    """Date psacc (« 2026-10-31 21:47:49+00:00 », UTC) -> datetime aware UTC."""
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def fmt_paris(dt):
    return dt.astimezone(PARIS).strftime(DATE_FMT)


def tempo_day(dt):
    """Jour Tempo d'un instant : date(t - 6h) en heure de Paris."""
    return (dt.astimezone(PARIS) - timedelta(hours=HC_END)).date()


def is_hc(dt):
    hour = dt.astimezone(PARIS).hour
    return hour >= HC_START or hour < HC_END


def next_boundary(dt):
    """Prochain changement de tranche (6h ou 22h, heure de Paris) strictement après dt."""
    day = dt.astimezone(PARIS).date()
    for d, h in ((day, HC_END), (day, HC_START), (day + timedelta(days=1), HC_END)):
        candidate = datetime.combine(d, dtime(h), tzinfo=PARIS).astimezone(UTC)
        if candidate > dt:
            return candidate
    raise AssertionError("unreachable")


def split_by_slot(start, end):
    """Découpe [start, end[ en (jour Tempo, 'HC'|'HP', secondes)."""
    t = start
    while t < end:
        nb = min(next_boundary(t), end)
        yield tempo_day(t), "HC" if is_hc(t) else "HP", (nb - t).total_seconds()
        t = nb


def to_serial(value):
    """Valeur de cellule -> numéro de série Sheets (pour comparer / trier des dates)."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        for fmt in (DATE_FMT, "%Y-%m-%d"):
            try:
                return (datetime.strptime(value, fmt) - SHEET_EPOCH).total_seconds() / 86400
            except ValueError:
                pass
    return None


def serial_to_day(value):
    if isinstance(value, (int, float)):
        return (SHEET_EPOCH + timedelta(days=float(value))).strftime("%Y-%m-%d")
    return str(value).strip()


# --------------------------------------------------------------------------- base psacc

def open_db_snapshot(config_dir, tmpdir, retries=5):
    """Copie info.db (+ WAL) dans tmpdir et l'ouvre : la base de psacc n'est jamais modifiée ni verrouillée."""
    names = ["info.db", "info.db-wal"]
    for attempt in range(retries):
        before = [os.stat(os.path.join(config_dir, n)) if os.path.exists(os.path.join(config_dir, n)) else None
                  for n in names]
        for n, st in zip(names, before):
            dst = os.path.join(tmpdir, n)
            if st:
                shutil.copyfile(os.path.join(config_dir, n), dst)
            elif os.path.exists(dst):
                os.remove(dst)
        after = [os.stat(os.path.join(config_dir, n)) if os.path.exists(os.path.join(config_dir, n)) else None
                 for n in names]
        if [(s.st_size, s.st_mtime_ns) if s else None for s in before] == \
                [(s.st_size, s.st_mtime_ns) if s else None for s in after]:
            conn = sqlite3.connect(os.path.join(tmpdir, "info.db"))
            conn.row_factory = sqlite3.Row
            if conn.execute("PRAGMA quick_check").fetchone()[0] == "ok":
                return conn
            conn.close()
        log.debug("base modifiée pendant la copie, nouvel essai (%d)", attempt + 1)
        time.sleep(2)
    raise RuntimeError("impossible d'obtenir une copie cohérente de info.db")


def load_car(config_dir, vin=None):
    """Véhicule électrique à suivre (cars.json de psacc) : VIN imposé ou premier véhicule avec batterie."""
    with open(os.path.join(config_dir, "cars.json"), encoding="utf-8") as f:
        cars = json.load(f)
    for car in cars:
        if (vin and car["vin"] == vin) or (not vin and (car.get("battery_power") or 0) > 0):
            return car
    raise RuntimeError(f"véhicule électrique introuvable dans cars.json (VIN={vin or 'auto'})")


def mileage_before(conn, vin, ts_raw):
    row = conn.execute("SELECT mileage FROM position WHERE VIN=? AND Timestamp<=? AND mileage IS NOT NULL "
                       "ORDER BY Timestamp DESC LIMIT 1", (vin, ts_raw)).fetchone()
    return row[0] if row else None


def energy_by_slot(start, stop, start_level, end_level, curve, kwh):
    """Répartit kwh entre (jour Tempo, tranche) avec la courbe de charge (au prorata du % gagné par segment),
    à défaut au prorata du temps."""
    points = [(start, start_level)]
    points += [(t, lvl) for t, lvl in curve if start < t < stop and lvl is not None]
    points.append((stop, end_level))
    segments = [(a[0], b[0], (b[1] - a[1]) if None not in (a[1], b[1]) else None) for a, b in zip(points, points[1:])]
    gains = [max(g, 0) for _, _, g in segments if g is not None]
    use_curve = len(curve) > 0 and len(gains) == len(segments) and sum(gains) > 0
    if use_curve:
        weights = [max(g, 0) for _, _, g in segments]
    else:
        weights = [(b - a).total_seconds() for a, b, _ in segments]
    total = sum(weights)
    result = {}
    if total <= 0:
        return result, use_curve
    for (a, b, _), w in zip(segments, weights):
        seg_kwh = kwh * w / total
        seg_s = (b - a).total_seconds()
        for day, slot, seconds in split_by_slot(a, b):
            result[(day, slot)] = result.get((day, slot), 0) + seg_kwh * seconds / seg_s
    return result, use_curve


def load_charges(conn, car):
    """Recharges terminées -> dict prêt à écrire (sans couleurs) ; renvoie aussi le nombre de charges en cours."""
    vin = car["vin"]
    charges, ongoing = [], 0
    for row in conn.execute("SELECT * FROM battery WHERE VIN=? ORDER BY start_at", (vin,)).fetchall():
        start, stop = parse_ts(row["start_at"]), parse_ts(row["stop_at"])
        if stop is None or row["end_level"] is None:
            ongoing += 1
            continue
        kwh = row["kw"]
        if kwh is None and row["start_level"] is not None:
            kwh = car["battery_power"] * (row["end_level"] - row["start_level"]) / 100
        kwh = max(float(kwh or 0), 0.0)
        curve = [(parse_ts(r["date"]), r["level"]) for r in conn.execute(
            "SELECT date, level FROM battery_curve WHERE VIN=? AND start_at=? ORDER BY date",
            (vin, row["start_at"])).fetchall()]
        by_slot, used_curve = energy_by_slot(start, stop, row["start_level"], row["end_level"], curve, kwh)
        mileage = row["mileage"] if row["mileage"] is not None else mileage_before(conn, vin, row["start_at"])
        charges.append({"id": "C-" + start.strftime("%Y%m%dT%H%M%SZ"), "start": start, "stop": stop,
                        "start_level": row["start_level"], "end_level": row["end_level"], "kwh": kwh,
                        "mileage": mileage, "by_slot": by_slot, "used_curve": used_curve})
    return charges, ongoing


def compute_trips(conn, car, now):
    """Portage de Trips.get_trips de psacc (véhicule électrique). Le dernier trajet n'est rendu que s'il est clos."""
    vin = car["vin"]
    rows = [dict(r) | {"ts": parse_ts(r["Timestamp"])} for r in conn.execute(
        "SELECT Timestamp, mileage, level FROM position WHERE VIN=? AND mileage IS NOT NULL "
        "AND Timestamp IS NOT NULL ORDER BY Timestamp", (vin,)).fetchall()]
    trips = []
    if len(rows) < 3:
        return trips

    def level_drop(a, b):
        return a["level"] - b["level"] if a["level"] is not None and b["level"] is not None else 0

    def is_recharging(a, b, distance):
        # +2 % toléré (régénération, température) ; au-delà de +5 % c'est une charge même si le compteur a bougé
        gain = -level_drop(a, b)
        return gain > 2 and (distance == 0 or gain > 5)

    def speed(distance, duration):
        return distance / duration if duration else 0

    def is_low_speed(spd, duration):
        return spd < 0.2 and duration > 0.05

    start, end = rows[0], rows[1]
    for x in range(len(rows) - 2):
        nxt = rows[x + 2]
        distance = end["mileage"] - start["mileage"]
        duration = (end["ts"] - start["ts"]).total_seconds() / 3600
        if is_low_speed(speed(distance, duration), duration) or is_recharging(start, end, distance):
            start = end
        else:
            distance = nxt["mileage"] - end["mileage"]
            duration = (nxt["ts"] - end["ts"]).total_seconds() / 3600
            end_trip, last = False, False
            if is_recharging(end, nxt, distance) or is_low_speed(speed(distance, duration), duration):
                end_trip = True
            elif duration > TRIP_MAX_GAP_H:
                end_trip = True
            elif x == len(rows) - 3:
                end, end_trip, last = nxt, True, True
            if end_trip:
                dist = end["mileage"] - start["mileage"]
                if dist > 0:
                    hours = (end["ts"] - start["ts"]).total_seconds() / 3600
                    kwh = max(level_drop(start, end), 0) * car["battery_power"] / 100
                    kwh_100 = 100 * kwh / dist
                    provisional = last and (now - end["ts"]) < timedelta(hours=TRIP_MAX_GAP_H)
                    if kwh_100 <= car.get("max_elec_consumption", 70) and speed(dist, hours) < TRIP_MAX_SPEED:
                        trips.append({"id": "T-" + start["ts"].strftime("%Y%m%dT%H%M%SZ"), "start": start["ts"],
                                      "end": end["ts"], "distance": dist, "kwh": kwh, "kwh_100": kwh_100,
                                      "mileage": end["mileage"], "provisional": provisional})
                start = nxt
        end = nxt
    return trips


def first_day(conn, vin):
    row = conn.execute("SELECT MIN(start_at) FROM battery WHERE VIN=?", (vin,)).fetchone()[0] or \
        conn.execute("SELECT MIN(Timestamp) FROM position WHERE VIN=?", (vin,)).fetchone()[0]
    return tempo_day(parse_ts(row)) if row else tempo_day(datetime.now(UTC))


# --------------------------------------------------------------------------- Tempo

def season(day):
    y = day.year if day.month >= 9 else day.year - 1
    return f"{y}-{y + 1}"


def fetch_tempo(api_base, start_day, today):
    """Couleurs connues de start_day à demain : {date: 'BLEU'|'BLANC'|'ROUGE'}."""
    seasons = sorted({season(start_day + timedelta(days=i)) for i in range((today - start_day).days + 2)})
    http = requests.Session()
    colors = {}
    resp = http.get(f"{api_base}/joursTempo", params=[("periode[]", s) for s in seasons], timeout=30)
    resp.raise_for_status()
    days = resp.json()
    for which in ("today", "tomorrow"):
        r = http.get(f"{api_base}/jourTempo/{which}", timeout=30)
        r.raise_for_status()
        days.append(r.json())
    for d in days:
        color = COLORS.get(d.get("codeJour"))
        day = date.fromisoformat(d["dateJour"])
        if color and start_day <= day <= today + timedelta(days=1):
            colors[day] = color
    return colors


# --------------------------------------------------------------------------- Google Sheets

class Sheets:
    API = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self, credentials_file, spreadsheet_id):
        creds = service_account.Credentials.from_service_account_file(
            credentials_file, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.session = AuthorizedSession(creds)
        self.base = f"{self.API}/{spreadsheet_id}"

    def _call(self, method, url, **kwargs):
        for attempt in range(6):
            resp = self.session.request(method, url, timeout=60, **kwargs)
            if resp.status_code not in (429, 500, 502, 503, 504):
                if not resp.ok:
                    raise RuntimeError(f"API Sheets {resp.status_code}: {resp.text[:500]}")
                return resp.json()
            delay = min(2 ** attempt * 5, 120)
            log.warning("API Sheets %s, nouvel essai dans %ss", resp.status_code, delay)
            time.sleep(delay)
        raise RuntimeError("API Sheets : quota dépassé de façon persistante")

    def grid(self):
        meta = self._call("GET", self.base, params={"fields": "sheets.properties(sheetId,title,gridProperties)"})
        return {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}

    def read(self, ranges):
        res = self._call("GET", f"{self.base}/values:batchGet",
                         params=[("ranges", r) for r in ranges] + [("valueRenderOption", "UNFORMATTED_VALUE"),
                                                                    ("dateTimeRenderOption", "SERIAL_NUMBER")])
        return [vr.get("values", []) for vr in res["valueRanges"]]

    def structure(self, reqs):
        if reqs:
            self._call("POST", f"{self.base}:batchUpdate", json={"requests": reqs})

    def write(self, data):
        if data:
            self._call("POST", f"{self.base}/values:batchUpdate",
                       json={"valueInputOption": "USER_ENTERED", "data": data})


def same_cell(a, b):
    if a in (None, "") and b in (None, ""):
        return True
    sa, sb = to_serial(a), to_serial(b)
    if sa is not None and sb is not None:
        return abs(sa - sb) < 5e-4
    return str(a).strip() == str(b).strip()


def plan_tab(tab, last_col, existing, rows, key_of_cell=str, sort_col=1):
    """Calcule l'upsert d'un onglet trié par date, en n'écrivant que dans A:<last_col> (aucune insertion de
    ligne : les formules du Sheet, dont U3 sur des plages fixes, ne doivent pas être décalées).

    Les lignes existantes sont mises à jour en place, les nouvelles ajoutées à leur rang chronologique ; une
    ligne existante (même saisie à la main) n'est déplacée que si une nouvelle doit s'intercaler avant elle.
    existing : valeurs A2:<last_col> lues ; rows : lignes à écrire (clé en colonne A).
    Renvoie (data, stats, lignes de données, lignes déplacées)."""
    width = {"B": 2, "G": 7, "N": 14}[last_col]
    n_existing = max((i + 1 for i, r in enumerate(existing) if r and r[0] not in ("", None)), default=0)
    existing = [list(r) + [""] * (width - len(r)) for r in existing[:n_existing]]

    # Ordre courant de la feuille ; une ligne sans date reprend la date précédente pour rester triable.
    layout, prev = [], float("-inf")
    for r in existing:
        s = to_serial(r[sort_col])
        prev = s if s is not None else prev
        layout.append({"key": key_of_cell(r[0]) if r[0] not in ("", None) else None, "sort": prev,
                       "old": r, "new": None})
    index = {item["key"]: item for item in layout if item["key"]}

    stats = {"inchangées": 0, "mises à jour": 0, "ajoutées": 0}
    for row in sorted(rows, key=lambda r: to_serial(r[sort_col])):
        key = row[0]
        if key in index:
            index[key]["new"] = row
            continue
        pos = bisect.bisect_right([item["sort"] for item in layout], to_serial(row[sort_col]))
        item = {"key": key, "sort": to_serial(row[sort_col]), "old": None, "new": row}
        layout.insert(pos, item)
        index[key] = item
        stats["ajoutées"] += 1

    data, moved = [], []
    for i, item in enumerate(layout):
        wanted = item["new"] if item["new"] is not None else item["old"]
        current = existing[i] if i < len(existing) else [""] * width
        if item["old"] is not None and item["old"] is not current:
            moved.append(item["key"])
        if item["old"] is not None and item["new"] is not None:
            same = all(same_cell(a, b) for a, b in zip(item["old"], item["new"]))
            stats["inchangées" if same else "mises à jour"] += 1
        if not all(same_cell(a, b) for a, b in zip(current, wanted)):
            data.append({"range": f"'{tab}'!A{i + 2}:{last_col}{i + 2}", "values": [wanted]})
    return data, stats, len(layout), moved


# --------------------------------------------------------------------------- synchro

def charge_row(c, colors):
    kwh_slots = {s: 0.0 for s in SLOTS}
    for (day, slot), kwh in c["by_slot"].items():
        kwh_slots[(colors[day], slot)] += kwh
    values = [round(kwh_slots[s], 3) for s in SLOTS]
    # L'arrondi ne doit pas faire diverger la somme H:M de la colonne F
    if values and c["kwh"] > 0:
        diff = round(round(c["kwh"], 3) - sum(values), 3)
        i = max(range(len(values)), key=values.__getitem__)
        values[i] = round(values[i] + diff, 3)
    return [c["id"], fmt_paris(c["start"]), fmt_paris(c["stop"]), c["start_level"], c["end_level"],
            round(c["kwh"], 3), round(c["mileage"], 1) if c["mileage"] is not None else "", *values, "psacc"]


def trip_row(t):
    return [t["id"], fmt_paris(t["start"]), fmt_paris(t["end"]), round(t["distance"], 1), round(t["kwh"], 2),
            round(t["kwh_100"], 2), round(t["mileage"], 1)]


def run_once(cfg, dry_run):
    now = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as tmp:
        conn = open_db_snapshot(cfg["config_dir"], tmp)
        try:
            car = load_car(cfg["config_dir"], cfg["vin"])
            charges, ongoing = load_charges(conn, car)
            trips = compute_trips(conn, car, now)
            start_day = first_day(conn, car["vin"])
        finally:
            conn.close()
    log.info("véhicule %s (…%s, %s kWh) : %d recharge(s) terminée(s), %d en cours, %d trajet(s)",
             car.get("label"), car["vin"][-6:], car["battery_power"], len(charges), ongoing, len(trips))

    colors = fetch_tempo(cfg["tempo_api"], start_day, now.astimezone(PARIS).date())
    log.info("couleurs Tempo connues : %d jour(s) du %s au %s", len(colors), start_day,
             max(colors) if colors else "-")

    charge_rows, pending = [], []
    for c in charges:
        missing = sorted({day for day, _ in c["by_slot"] if day not in colors})
        if missing:
            pending.append((c["id"], missing))
            continue
        charge_rows.append(charge_row(c, colors))
        if not c["used_curve"]:
            log.debug("%s : pas de courbe de charge, répartition au prorata du temps", c["id"])
    for cid, missing in pending:
        log.info("%s en attente : couleur inconnue pour %s", cid, ", ".join(map(str, missing)))
    trip_rows = [trip_row(t) for t in trips if not t["provisional"]]
    if len(trip_rows) < len(trips):
        log.info("dernier trajet encore ouvert, repris au prochain passage")

    sheets = Sheets(cfg["credentials"], cfg["spreadsheet_id"])
    grid = sheets.grid()
    ex_colors, ex_charges, ex_trips = sheets.read([f"'{TAB_COLORS}'!A2:B", f"'{TAB_CHARGES}'!A2:N",
                                                   f"'{TAB_TRIPS}'!A2:G"])
    color_rows = [[d.isoformat(), c] for d, c in sorted(colors.items())]
    plans = {
        TAB_COLORS: plan_tab(TAB_COLORS, "B", ex_colors, color_rows, key_of_cell=serial_to_day, sort_col=0),
        TAB_CHARGES: plan_tab(TAB_CHARGES, "N", ex_charges, charge_rows),
        TAB_TRIPS: plan_tab(TAB_TRIPS, "G", ex_trips, trip_rows),
    }

    structure, data = [], []
    for tab, (tab_data, stats, n_rows, moved) in plans.items():
        props = grid[tab]
        log.info("%s : %s", tab, ", ".join(f"{v} {k}" for k, v in stats.items()))
        if moved:
            log.warning("%s : %d ligne(s) décalée(s) d'un rang pour garder l'ordre chronologique (%s…) ; "
                        "vérifier les notes manuelles des colonnes suivantes", tab, len(moved), moved[0])
        missing_rows = n_rows + 1 - props["gridProperties"]["rowCount"]
        if missing_rows > 0:
            structure.append({"appendDimension": {"sheetId": props["sheetId"], "dimension": "ROWS",
                                                  "length": missing_rows + 100}})
        data += tab_data
        for d in tab_data[:5] if dry_run else []:
            log.info("  [dry-run] %s <- %s", d["range"], d["values"][0])
        if dry_run and len(tab_data) > 5:
            log.info("  [dry-run] … et %d autre(s) plage(s)", len(tab_data) - 5)

    if dry_run:
        log.info("dry-run : %d ajout(s) de lignes à la grille et %d plage(s) non écrites", len(structure), len(data))
        return
    sheets.structure(structure)
    sheets.write(data)
    log.info("synchro terminée : %d opération(s) de structure, %d plage(s) écrites", len(structure), len(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="calcule et affiche sans écrire dans le Sheet")
    parser.add_argument("--once", action="store_true", help="un seul passage puis sortie")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug or os.environ.get("DEBUG") else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    cfg = {
        "config_dir": os.environ.get("PSACC_CONFIG_DIR", "/config"),
        "credentials": os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/google-sa.json"),
        "spreadsheet_id": os.environ.get("SPREADSHEET_ID") or parser.error("SPREADSHEET_ID manquant"),
        "vin": os.environ.get("VIN") or None,
        "tempo_api": os.environ.get("TEMPO_API_URL", "https://www.api-couleur-tempo.fr/api"),
    }
    interval = int(os.environ.get("SYNC_INTERVAL", "3600"))
    dry_run = args.dry_run or os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

    while True:
        started = time.monotonic()
        try:
            run_once(cfg, dry_run)
        except Exception:  # pylint: disable=broad-except
            log.exception("échec de la synchro")
            if args.once:
                sys.exit(1)
        if args.once:
            return
        time.sleep(max(interval - (time.monotonic() - started), 60))


if __name__ == "__main__":
    main()
