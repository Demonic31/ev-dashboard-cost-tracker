#!/usr/bin/env python3
"""Front web en lecture seule du Google Sheet de suivi e-208 / Tempo.

Sert une page HTML et un endpoint /api/data qui renvoie, en JSON, les valeurs calculées par le Sheet
(onglets Dashboard, Recharges, Trajets, Couleurs Tempo). Aucune donnée n'est recalculée ici : la page est un
miroir du Sheet, rafraîchi au plus toutes les CACHE_SECONDS.

En complément : la saison Tempo en cours (api-couleur-tempo.fr, comme la synchro) et les paramètres du comparatif
des offres (prix des autres options EDF, prix moyen du carburant sur data.economie.gouv.fr).
"""
import json
import logging
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

SHEET_EPOCH = datetime(1899, 12, 30)
COLORS = {1: "BLEU", 2: "BLANC", 3: "ROUGE"}
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Plages lues et colonnes contenant des dates (index 0-based) à convertir en ISO
RANGES = {
    "kpi": ("Dashboard!A3:I4", ()),
    "months": ("Dashboard!A7:F40", ()),
    "tranches": ("Dashboard!H7:J13", ()),
    "charges": ("Recharges!A1:W", (1, 2)),
    "trips": ("Trajets!A1:H", (1, 2)),
    "colors": ("'Couleurs Tempo'!A1:B", (0,)),
    "settings": ("'Paramètres'!B10:B12", ()),
}

log = logging.getLogger("e208-dashboard")


def serial_to_iso(value):
    if isinstance(value, (int, float)):
        dt = SHEET_EPOCH + timedelta(days=float(value))
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    return value


def cached(seconds):
    """Mémoïse le résultat d'une méthode sans argument pendant `seconds` (force=True pour relire)."""
    def deco(fn):
        state = {"at": 0.0, "value": None, "lock": threading.Lock()}

        def wrapper(self, force=False):
            with state["lock"]:
                if force or state["value"] is None or time.monotonic() - state["at"] > seconds:
                    state["value"], state["at"] = fn(self), time.monotonic()
                    if isinstance(state["value"], dict) and "error" in state["value"]:
                        state["at"] -= seconds - 60  # erreur : nouvel essai dans une minute
                return state["value"]
        return wrapper
    return deco


class SheetMirror:
    def __init__(self, credentials_file, spreadsheet_id, cache_seconds):
        creds = service_account.Credentials.from_service_account_file(
            credentials_file, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        self.session = AuthorizedSession(creds)
        self.url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values:batchGet"
        self.cache_seconds = cache_seconds
        self.lock = threading.Lock()
        self.cached, self.cached_at = None, 0.0

    def fetch(self):
        params = [("ranges", r) for r, _ in RANGES.values()]
        params += [("valueRenderOption", "UNFORMATTED_VALUE"), ("dateTimeRenderOption", "SERIAL_NUMBER")]
        resp = self.session.get(self.url, params=params, timeout=30)
        if not resp.ok:
            raise RuntimeError(f"API Sheets {resp.status_code}: {resp.text[:300]}")
        out = {}
        for (name, (_, date_cols)), vr in zip(RANGES.items(), resp.json()["valueRanges"]):
            rows = vr.get("values", [])
            out[name] = [[serial_to_iso(v) if i in date_cols else v for i, v in enumerate(row)] for row in rows]
        return shape(out)

    def get(self, force=False):
        with self.lock:
            if force or self.cached is None or time.monotonic() - self.cached_at > self.cache_seconds:
                data = self.fetch()
                data["fetched_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                self.cached, self.cached_at = data, time.monotonic()
                log.info("Sheet relu : %d recharge(s), %d trajet(s), %d jour(s) Tempo",
                         len(data["charges"]["rows"]), len(data["trips"]["rows"]), len(data["colors"]))
            return self.cached


def setting(raw, i, default):
    rows = raw["settings"]
    return rows[i][0] if len(rows) > i and rows[i] and isinstance(rows[i][0], (int, float)) else default


def shape(raw):
    """Met les plages brutes en forme pour la page (en-têtes séparés, lignes vides retirées)."""
    def table(rows):
        headers = rows[0] if rows else []
        body = [r + [""] * (len(headers) - len(r)) for r in rows[1:] if r and r[0] not in ("", None)]
        return {"headers": headers, "rows": body}

    kpi = raw["kpi"]
    kpi_headers = kpi[0] if kpi else []
    kpi_values = (kpi[1] if len(kpi) > 1 else []) + [""] * len(kpi_headers)
    months = [r for r in raw["months"][1:] if r and isinstance(r[0], str) and len(r[0]) == 7 and r[0][4] == "-"]
    tranches = [{"tranche": r[0], "kwh": r[1] if len(r) > 1 else 0, "part": r[2] if len(r) > 2 else ""}
                for r in raw["tranches"][1:] if r]
    colors = [{"day": r[0][:10], "color": r[1]} for r in raw["colors"][1:]
              if len(r) > 1 and isinstance(r[0], str)]
    return {
        "kpi": [{"label": h, "value": v} for h, v in zip(kpi_headers, kpi_values)],
        "months": [{"month": r[0], "charges": r[1], "kwh": r[2], "cost": r[3], "km": r[4],
                    "eur_100km": r[5] if len(r) > 5 else ""} for r in (m + [""] * 6 for m in months)],
        "tranches": tranches,
        "charges": table(raw["charges"]),
        "trips": table(raw["trips"]),
        "colors": colors,
        # Paramètres!B10:B12 : début HC (h), fin HC (h), coefficient de pertes
        "hc_hours": [setting(raw, 0, 22), setting(raw, 1, 6)],
        "loss_coef": setting(raw, 2, 1),
    }


def season_of(day):
    y = day.year if day.month >= 9 else day.year - 1
    return f"{y}-{y + 1}"


class TempoSeason:
    """Saison Tempo en cours (1er septembre → 31 août) : jours consommés / restants et calendrier."""

    def __init__(self, api_base):
        self.api_base = api_base.rstrip("/")
        self.http = requests.Session()

    @cached(3600)
    def get(self):
        try:
            periode = season_of(date.today())
            stats = self.http.get(f"{self.api_base}/stats", timeout=30)
            stats.raise_for_status()
            days = self.http.get(f"{self.api_base}/joursTempo", params=[("periode[]", periode)], timeout=30)
            days.raise_for_status()
            log.info("saison Tempo %s relue", periode)
            return {"periode": periode, "stats": stats.json(),
                    "days": [{"day": d["dateJour"], "color": COLORS.get(d.get("codeJour"))} for d in days.json()]}
        except (requests.RequestException, ValueError, KeyError) as exc:
            log.warning("API Tempo indisponible : %s", exc)
            return {"error": str(exc)}


FUEL_API = ("https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
            "prix-des-carburants-en-france-flux-instantane-v2/records")


class FuelPrice:
    """Prix moyen d'un carburant (stations mises à jour ces `days` derniers jours), France ou un département."""

    def __init__(self, fuel, departement, days=3):
        if not (fuel.isalnum() and (not departement or departement.isalnum())):
            raise ValueError("PETROL_FUEL / PETROL_DEPARTEMENT invalides")
        self.fuel, self.departement, self.days = fuel.lower(), departement, days
        self.http = requests.Session()

    @cached(7 * 24 * 3600)  # une relève par semaine
    def get(self):
        where = f"{self.fuel}_maj >= now(days=-{self.days})"
        if self.departement:
            where += f' and code_departement = "{self.departement}"'
        try:
            resp = self.http.get(FUEL_API, timeout=30, params={
                "select": f"avg({self.fuel}_prix) as prix, count({self.fuel}_prix) as stations", "where": where, "limit": 1})
            resp.raise_for_status()
            row = resp.json()["results"][0]
            if not row.get("prix"):
                raise ValueError("aucun prix publié")
            log.info("prix %s relu : %.3f €/L (%d stations)", self.fuel.upper(), row["prix"], row["stations"])
            return {"fuel": self.fuel.upper(), "price": row["prix"], "stations": row["stations"], "days": self.days,
                    "zone": f"département {self.departement}" if self.departement else "France",
                    "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            log.warning("prix carburant indisponible : %s", exc)
            return {"error": str(exc)}


def env_price(name):
    value = os.environ.get(name, "").strip().replace(",", ".")
    return float(value) if value else None


def offers_config():
    """Prix des options EDF à comparer avec Tempo et consommation de la voiture thermique de référence."""
    return {"base": env_price("BASE_PRICE_KWH"),
            "hchp": {"hc": env_price("HCHP_HC_PRICE_KWH"), "hp": env_price("HCHP_HP_PRICE_KWH")},
            "zen": {"low": env_price("ZEN_LOW_PRICE_KWH"), "high": env_price("ZEN_HIGH_PRICE_KWH")},
            "petrol_l_100km": env_price("PETROL_L_100KM")}


def make_handler(mirror, season, fuel, offers, battery_kwh):
    class Handler(BaseHTTPRequestHandler):
        server_version = "e208-dashboard"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    self.send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/api/data":
                try:
                    force = "refresh=1" in self.path
                    data = dict(mirror.get(force=force), season=season.get(force=force), battery_kwh=battery_kwh,
                                offers=dict(offers, fuel=fuel.get() if offers["petrol_l_100km"] else None))
                    self.send(200, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                except Exception as exc:  # pylint: disable=broad-except
                    log.exception("lecture du Sheet impossible")
                    self.send(502, json.dumps({"error": str(exc)}).encode(), "application/json")
            elif path == "/healthz":
                self.send(200, b"ok", "text/plain")
            else:
                self.send(404, b"not found", "text/plain")

    return Handler


def main():
    logging.basicConfig(level=logging.DEBUG if os.environ.get("DEBUG") else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    spreadsheet_id = os.environ.get("SPREADSHEET_ID") or sys.exit("SPREADSHEET_ID manquant")
    mirror = SheetMirror(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/google-sa.json"),
                         spreadsheet_id, int(os.environ.get("CACHE_SECONDS", "300")))
    season = TempoSeason(os.environ.get("TEMPO_API_URL", "https://www.api-couleur-tempo.fr/api"))
    fuel = FuelPrice(os.environ.get("PETROL_FUEL", "sp98"), os.environ.get("PETROL_DEPARTEMENT", "").strip())
    offers = offers_config()
    port = int(os.environ.get("PORT", "8080"))
    log.info("front e-208 sur le port %d (comparatif : %s)", port, json.dumps(offers))
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(mirror, season, fuel, offers, env_price("BATTERY_KWH"))).serve_forever()


if __name__ == "__main__":
    main()
