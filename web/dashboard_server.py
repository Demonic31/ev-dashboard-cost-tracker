#!/usr/bin/env python3
"""Front web en lecture seule du Google Sheet de suivi e-208 / Tempo.

Sert une page HTML et un endpoint /api/data qui renvoie, en JSON, les valeurs calculées par le Sheet
(onglets Dashboard, Recharges, Trajets, Couleurs Tempo). Aucune donnée n'est recalculée ici : la page est un
miroir du Sheet, rafraîchi au plus toutes les CACHE_SECONDS.

En complément : la saison Tempo en cours (api-couleur-tempo.fr, comme la synchro) et les paramètres du comparatif
des offres (prix des autres options EDF, prix moyen du carburant sur data.economie.gouv.fr) et, pour l'équivalent
essence de chaque recharge, le prix moyen du carburant le jour de la recharge (archives journalières officielles).

Mire de connexion facultative : un mot de passe unique dont l'empreinte (scrypt) est fournie au conteneur
(DASHBOARD_PASSWORD_HASH, générée avec --hash-password), complété si besoin d'un code TOTP à 6 chiffres
d'application d'authentification (DASHBOARD_TOTP_SECRET, généré avec --setup-totp).
"""
import base64
import getpass
import hashlib
import hmac
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

SHEET_EPOCH = datetime(1899, 12, 30)
COLORS = {1: "BLEU", 2: "BLANC", 3: "ROUGE"}
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
# Accessibles sans connexion : ce dont la page de connexion a besoin
PUBLIC_PATHS = ("/healthz", "/sw.js", "/static/theme.js", "/static/login.js", "/static/pwa.js",
                "/static/favicon.svg", "/static/favicon-test.svg", "/static/apple-touch-icon.png",
                "/static/manifest.webmanifest", "/static/icon-192.png", "/static/icon-512.png",
                "/static/icon-maskable-512.png", "/static/icon-192-test.png", "/static/icon-512-test.png",
                "/static/icon-maskable-512-test.png", "/static/offline.html")
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                ".png": "image/png", ".html": "text/html; charset=utf-8",
                ".webmanifest": "application/manifest+json"}
# Libellé ajouté au nom de l'app installée (PWA), ex. « PREPROD » : icônes et logo de la mire marqués « TEST » ;
# vide en PROD
APP_LABEL = os.environ.get("APP_LABEL", "").strip()
# Tout est servi depuis le conteneur (Chart.js et logo compris) : pas de script ni de ressource externe
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                               "form-action 'self'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}

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
# Archives journalières officielles du site prix-carburants.gouv.fr (zip d'un XML, ~1 Mo) : prix du jour J
FUEL_ARCHIVE = "https://donnees.roulez-eco.fr/opendata/jour/{:%Y%m%d}"
FUEL_NAMES = {"sp98": "SP98", "sp95": "SP95", "e10": "E10", "e85": "E85", "gazole": "Gazole", "gplc": "GPLc"}


class Psacc:
    """Lecture seule de l'API web de psacc (réseau Docker interne), sans appel aux serveurs Stellantis."""

    def __init__(self, url, vin):
        self.url, self._vin = url.rstrip("/"), vin

    def vin(self):
        if not self._vin:
            cars = requests.get(f"{self.url}/get_vehicles", timeout=10).json()
            self._vin = next(c["vin"] for c in cars if (c.get("battery_power") or 0) > 0)
        return self._vin

    def get(self, path):
        return requests.get(f"{self.url}{path}", timeout=15).json()


class BatteryHealth:
    """SOH de la batterie relevé par psacc (champ « resistance » de l'API Stellantis)."""

    def __init__(self, psacc):
        self.psacc = psacc

    @cached(3600)
    def get(self):
        try:
            soh = self.psacc.get(f"/battery/soh/{self.psacc.vin()}").get("soh")
            if soh is None:
                return {"error": "aucun SOH relevé par psacc"}
            return {"soh": soh, "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        except (requests.RequestException, ValueError, StopIteration) as exc:
            log.warning("SOH indisponible : %s", exc)
            return {"error": str(exc) or type(exc).__name__}


class CarStatus:
    """Dernier état connu de la voiture (niveau, autonomie, charge, compteur), lu dans le cache de psacc
    (from_cache=1 : la voiture n'est pas réveillée, les valeurs datent de son dernier envoi)."""

    def __init__(self, psacc):
        self.psacc = psacc

    @cached(300)
    def get(self):
        try:
            info = self.psacc.get(f"/get_vehicleinfo/{self.psacc.vin()}?from_cache=1")
            elec = next(e for e in info.get("energy") or [] if e.get("type") == "Electric")
            charging = elec.get("charging") or {}
            odo = info.get("timed_odometer") or {}
            return {"level": elec.get("level"), "autonomy": elec.get("autonomy"), "updated_at": elec.get("updated_at"),
                    "status": charging.get("status"), "plugged": charging.get("plugged"),
                    "charging_rate": charging.get("charging_rate"), "remaining_time": charging.get("remaining_time"),
                    "mileage": odo.get("mileage"), "mileage_at": odo.get("updated_at")}
        except (requests.RequestException, ValueError, StopIteration) as exc:
            log.warning("état de la voiture indisponible : %s", exc)
            return {"error": str(exc) or type(exc).__name__}


class ChargeCurves:
    """Points de la courbe de charge relevés par psacc (table battery_curve), par ID de recharge du Sheet.
    La base est copiée (avec son WAL) avant lecture : celle de psacc n'est jamais verrouillée ni modifiée."""

    def __init__(self, psacc, config_dir):
        self.psacc, self.config_dir = psacc, config_dir

    @staticmethod
    def iso(ts):
        return datetime.fromisoformat(ts).astimezone(timezone.utc).isoformat(timespec="seconds")

    @cached(300)
    def get(self):
        try:
            vin = self.psacc.vin()
            with tempfile.TemporaryDirectory() as tmp:
                for name in ("info.db", "info.db-wal"):
                    if os.path.exists(os.path.join(self.config_dir, name)):
                        shutil.copyfile(os.path.join(self.config_dir, name), os.path.join(tmp, name))
                conn = sqlite3.connect(os.path.join(tmp, "info.db"))
                try:
                    charges = conn.execute("SELECT start_at, stop_at, start_level, end_level FROM battery "
                                           "WHERE VIN=? AND stop_at IS NOT NULL", (vin,)).fetchall()
                    curves = {}
                    for start, stop, start_level, end_level in charges:
                        points = [[self.iso(d), lvl, aut] for d, lvl, aut in conn.execute(
                            "SELECT date, level, autonomy FROM battery_curve WHERE VIN=? AND start_at=? ORDER BY date",
                            (vin, start)).fetchall()]
                        # début et fin de la table battery : bornes réelles de la charge
                        if start_level is not None and (not points or points[0][0] > self.iso(start)):
                            points.insert(0, [self.iso(start), start_level, None])
                        if end_level is not None and (not points or points[-1][0] < self.iso(stop)):
                            points.append([self.iso(stop), end_level, None])
                        sid = "C-" + datetime.fromisoformat(start).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        curves[sid] = points
                    ongoing = self.ongoing(conn, vin)
                finally:
                    conn.close()
            rate, count = self.median_rate(charges)
            return {"curves": curves, "ongoing": ongoing, "median_rate": rate, "median_count": count}
        except (OSError, sqlite3.Error, requests.RequestException, ValueError, StopIteration) as exc:
            log.warning("courbes de charge indisponibles : %s", exc)
            return {"error": str(exc) or type(exc).__name__}


    def ongoing(self, conn, vin):
        """Points de la charge en cours (ligne de battery sans stop_at), début de charge compris."""
        row = conn.execute("SELECT start_at, start_level FROM battery WHERE VIN=? AND stop_at IS NULL "
                           "ORDER BY start_at DESC LIMIT 1", (vin,)).fetchone()
        if not row:
            return None
        start, start_level = row
        points = [[self.iso(d), lvl] for d, lvl in conn.execute(
            "SELECT date, level FROM battery_curve WHERE VIN=? AND start_at=? ORDER BY date", (vin, start)).fetchall()
            if lvl is not None]
        if start_level is not None and (not points or points[0][0] > self.iso(start)):
            points.insert(0, [self.iso(start), start_level])
        return {"start_at": self.iso(start), "points": points}

    @staticmethod
    def median_rate(charges, count=10):
        """Vitesse de charge médiane (% par heure) des dernières recharges terminées d'au moins 30 min, et le nombre
        de recharges retenues."""
        rates = []
        for start, stop, start_level, end_level in sorted(charges)[-count:]:
            hours = (datetime.fromisoformat(stop) - datetime.fromisoformat(start)).total_seconds() / 3600
            if start_level is not None and end_level is not None and hours >= 0.5 and end_level > start_level:
                rates.append((end_level - start_level) / hours)
        rates.sort()
        return (rates[len(rates) // 2] if rates else None), len(rates)


def iso_hours(duration):
    """Durée ISO 8601 de l'API Stellantis (« PT2H25M ») en heures, None si absente ou illisible."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", duration or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, sec = (float(g or 0) for g in m.groups())
    return 24 * d + h + mi / 60 + sec / 3600


def charge_estimate(car, curves):
    """Base de l'estimation d'une charge en cours, extrapolée dans la page (la voiture n'envoie son état qu'au
    début, vers +45 min, à la fin et aux réveils) : dernier relevé et vitesse de charge en % par heure.

    Vitesse : mesurée dans la charge en cours (deux relevés à 1 h d'écart au moins), sinon médiane des dernières
    recharges, sinon déduite du temps restant annoncé par la voiture (souvent optimiste)."""
    if car.get("error") or car.get("status") != "InProgress" or car.get("level") is None or not car.get("updated_at"):
        return None
    at, level = datetime.fromisoformat(car["updated_at"]), float(car["level"])
    car_hours = iso_hours(car.get("remaining_time"))
    rate, source = None, None
    points = ((curves or {}).get("ongoing") or {}).get("points") or []
    if len(points) >= 2:
        (t_a, l_a), (t_b, l_b) = points[0], points[-1]
        hours = (datetime.fromisoformat(t_b) - datetime.fromisoformat(t_a)).total_seconds() / 3600
        if hours >= 1 and l_b > l_a:
            rate, source = (l_b - l_a) / hours, f"mesuré sur la charge en cours, {len(points)} relevés"
    if rate is None and (curves or {}).get("median_rate"):
        count = curves.get("median_count") or 0
        rate = curves["median_rate"]
        source = f"médiane des {count} dernières recharges" if count > 1 else "dernière recharge"
    if rate is None and car_hours and level < 100:
        rate, source = (100 - level) / car_hours, "d'après le temps restant annoncé par la voiture"
    if not rate:
        return None
    return {"from_at": at.astimezone(timezone.utc).isoformat(timespec="seconds"), "from_level": level,
            "from_autonomy": car.get("autonomy"), "rate": round(rate, 3), "source": source, "target": 100,
            "car_end_at": (at + timedelta(hours=car_hours)).astimezone(timezone.utc).isoformat(timespec="seconds")
            if car_hours else None}


class FuelPrice:
    """Prix moyen d'un carburant (stations mises à jour ces `days` derniers jours), France ou un département."""

    def __init__(self, fuel, departement, days=3):
        if not (fuel.isalnum() and (not departement or departement.isalnum())):
            raise ValueError("PETROL_FUEL / PETROL_DEPARTEMENT invalides")
        self.fuel, self.departement, self.days = fuel.lower(), departement, days
        self.http = requests.Session()

    @cached(24 * 3600)  # une relève par jour
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


class FuelHistory:
    """Prix moyen d'un carburant à une date passée, pour l'équivalent essence de chaque recharge.

    Même calcul que FuelPrice (moyenne des stations mises à jour les `days` derniers jours, France ou département),
    appliqué à l'archive officielle du jour. Les jours manquants sont téléchargés en tâche de fond, un par un ;
    les prix trouvés sont gardés dans DATA_DIR (volume Docker) pour ne pas être retéléchargés au redémarrage.
    """

    def __init__(self, fuel, departement, data_dir, days=3):
        self.name, self.days = FUEL_NAMES.get(fuel.lower()), days
        # le code postal commence par le numéro de département (Corse : 20 pour 2A et 2B)
        self.cp = "20" if departement.upper() in ("2A", "2B") else departement
        self.file = os.path.join(data_dir, f"fuel-{fuel.lower()}-{departement or 'fr'}.json") if data_dir else None
        self.prices, self.pending, self.failed = {}, [], {}
        self.lock, self.wake = threading.Lock(), threading.Event()
        self.http = requests.Session()
        if self.file and os.path.isfile(self.file):
            try:
                with open(self.file, encoding="utf-8") as f:
                    self.prices = json.load(f)
                log.info("historique %s : %d jour(s) en cache", self.name, len(self.prices))
            except (OSError, ValueError) as exc:
                log.warning("historique carburant illisible (%s) : repart de zéro", exc)
        if self.name:
            threading.Thread(target=self.worker, name="fuel-history", daemon=True).start()

    def get(self, days):
        """Prix connus pour ces jours (AAAA-MM-JJ) ; les jours passés manquants sont mis en file d'attente."""
        if not self.name:
            return {}
        today = date.today().isoformat()
        with self.lock:
            missing = sorted(d for d in days if d < today and d not in self.prices and d not in self.pending
                             and time.monotonic() - self.failed.get(d, -1e9) > 6 * 3600)
            self.pending += missing
        if missing:
            self.wake.set()
        return {d: self.prices[d] for d in days if d in self.prices}

    def worker(self):
        while True:
            self.wake.wait()
            self.wake.clear()
            while True:
                with self.lock:
                    if not self.pending:
                        break
                    day = self.pending[-1]  # les plus récents d'abord
                try:
                    price = self.fetch(date.fromisoformat(day))
                    with self.lock:
                        self.prices[day] = price
                    log.info("prix %s du %s : %.3f €/L (%d stations)",
                             self.name, day, price["price"], price["stations"])
                    self.save()
                except (requests.RequestException, zipfile.BadZipFile, ET.ParseError, ValueError, OSError) as exc:
                    log.warning("prix %s du %s indisponible : %s", self.name, day, exc)
                    with self.lock:
                        self.failed[day] = time.monotonic()  # nouvel essai dans 6 h
                finally:
                    with self.lock:
                        self.pending.remove(day)
                time.sleep(2)  # sans presse : un fichier toutes les deux secondes

    def fetch(self, day):
        resp = self.http.get(FUEL_ARCHIVE.format(day), timeout=60)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            member = z.infolist()[0]
            if member.file_size > 200 * 1024 * 1024:
                raise ValueError("archive anormalement grosse")
            root = ET.fromstring(z.read(member))
        since = (day - timedelta(days=self.days - 1)).isoformat()
        values = [float(p.get("valeur")) for pdv in root.iter("pdv") if pdv.get("cp", "").startswith(self.cp)
                  for p in pdv.iter("prix") if p.get("nom") == self.name and (p.get("maj") or "")[:10] >= since]
        if not values:
            raise ValueError("aucun prix publié")
        return {"price": round(sum(values) / len(values), 4), "stations": len(values)}

    def save(self):
        if not self.file:
            return
        try:
            with self.lock:
                body = json.dumps(self.prices, sort_keys=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp, self.file)
        except OSError as exc:
            log.warning("historique carburant non enregistré : %s", exc)


def env_price(name):
    value = os.environ.get(name, "").strip().replace(",", ".")
    return float(value) if value else None


def offers_config():
    """Prix des options EDF à comparer avec Tempo et consommation de la voiture thermique de référence."""
    return {"base": env_price("BASE_PRICE_KWH"),
            "hchp": {"hc": env_price("HCHP_HC_PRICE_KWH"), "hp": env_price("HCHP_HP_PRICE_KWH")},
            "zen": {"low": env_price("ZEN_LOW_PRICE_KWH"), "high": env_price("ZEN_HIGH_PRICE_KWH")},
            "petrol_l_100km": env_price("PETROL_L_100KM")}


def hash_password(password, n=2 ** 15, r=8, p=1):
    """Empreinte scrypt « scrypt:n:r:p:sel:clé » (hexadécimal, sans « $ » pour rester lisible par Compose)."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt:{n}:{r}:{p}:{salt.hex()}:{key.hex()}"


def totp_code(secret, step):
    """Code TOTP (RFC 6238 : HMAC-SHA1, 6 chiffres) de la tranche de 30 s numéro `step`."""
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(int.from_bytes(digest[offset:offset + 4], 'big') & 0x7FFFFFFF) % 1_000_000:06d}"


class PasswordGate:
    """Mire de connexion à mot de passe unique.

    Désactivée si aucune empreinte n'est fournie. Sinon, la connexion est exigée pour les requêtes qui passent par
    le reverse proxy (TRUSTED_PROXY), ou pour toutes si LOGIN_SCOPE=all ou si le proxy n'est pas renseigné.
    TRUSTED_PROXY est une IP (Synology…) ou un nom résolu par le DNS de Docker (« caddy », le proxy du profil
    « proxy ») : un proxy qui change d'IP au redémarrage reste reconnu.
    Une requête qui porte des en-têtes de reverse proxy (X-Forwarded-For, X-Real-IP…) sans venir de TRUSTED_PROXY
    est aussi traitée comme venant d'Internet (mire exigée, en-têtes ignorés) : un proxy oublié dans TRUSTED_PROXY
    n'ouvre pas le dashboard. Sans mire, ces requêtes sont refusées : jamais de dashboard public ouvert.
    La session est un cookie signé (HMAC) qui porte son expiration ; changer le mot de passe invalide les sessions.
    Avec une clé TOTP, un code d'application d'authentification est aussi demandé (tranche de 30 s en cours,
    précédente ou suivante ; un code déjà utilisé est refusé).
    Après MAX_FAILURES échecs en BLOCK_SECONDS, l'IP du visiteur (X-Real-IP posé par le proxy) est bloquée."""

    COOKIE = "e208_session"
    MAX_FAILURES, BLOCK_SECONDS = 5, 15 * 60
    SESSION_SECONDS, REMEMBER_SECONDS = 12 * 3600, 30 * 86400
    RENEW_AFTER = 86400  # une session « se souvenir » utilisée après 1 jour repart pour 30 jours

    def __init__(self, password_hash, secret, trusted_proxy, scope, totp_secret=""):
        self.enabled = bool(password_hash)
        self.totp_secret, self.last_totp_step = totp_secret.replace(" ", "").upper(), 0
        self.trusted_proxy, self.scope = trusted_proxy, scope
        self.proxy_ips, self.resolved_at = set(), -1e9
        self.failures, self.lock = {}, threading.Lock()
        if not self.enabled:
            return
        _, n, r, p, salt, key = password_hash.split(":")
        self.params, self.salt, self.key = (int(n), int(r), int(p)), bytes.fromhex(salt), bytes.fromhex(key)
        if not secret:
            log.warning("SESSION_SECRET absent : clé aléatoire, les sessions ne survivront pas au redémarrage")
        self.secret = (secret or secrets.token_hex(32)).encode()
        # changer le mot de passe ou la clé TOTP invalide les sessions existantes
        self.fingerprint = hashlib.sha256(f"{password_hash}|{self.totp_secret}".encode()).hexdigest()[:16]

    def via_proxy(self, addr):
        if not self.trusted_proxy:
            return False
        try:
            return ipaddress.ip_address(addr) == ipaddress.ip_address(self.trusted_proxy)
        except ValueError:
            pass  # un nom (service Docker) plutôt qu'une IP
        if addr not in self.proxy_ips or time.monotonic() - self.resolved_at > 60:
            with self.lock:
                if time.monotonic() - self.resolved_at > 5:  # au plus une résolution toutes les 5 s
                    try:
                        self.proxy_ips = set(socket.gethostbyname_ex(self.trusted_proxy)[2])
                    except OSError:
                        self.proxy_ips = set()  # proxy absent (profil non lancé) : aucune requête n'en vient
                    self.resolved_at = time.monotonic()
        return addr in self.proxy_ips

    PROXY_HEADERS = ("X-Forwarded-For", "X-Real-IP", "X-Forwarded-Proto", "X-Forwarded-Host", "Forwarded")

    def remote(self, addr, headers):
        """Requête venue d'Internet : par le proxy de confiance, ou par un proxy non déclaré (en-têtes de proxy)."""
        return self.via_proxy(addr) or any(headers.get(h) for h in self.PROXY_HEADERS)

    def exposed_without_login(self, addr, headers):
        """Requête venue d'Internet alors qu'aucun mot de passe n'est configuré."""
        return not self.enabled and self.remote(addr, headers)

    def client_ip(self, addr, headers):
        return (headers.get("X-Real-IP") or addr).strip() if self.via_proxy(addr) else addr

    def insecure(self, addr, headers):
        """Requête arrivée en HTTP sur le proxy (X-Forwarded-Proto posé par le Synology) : à rediriger en HTTPS."""
        return self.via_proxy(addr) and (headers.get("X-Forwarded-Proto") or "").lower() == "http"

    def required(self, addr, headers):
        return self.enabled and (self.scope == "all" or not self.trusted_proxy or self.remote(addr, headers))

    def check(self, password):
        n, r, p = self.params
        key = hashlib.scrypt(password.encode(), salt=self.salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024,
                             dklen=len(self.key))
        return hmac.compare_digest(key, self.key)

    def check_code(self, code):
        """Code TOTP valable et pas encore utilisé (toujours vrai sans clé TOTP)."""
        if not self.totp_secret:
            return True
        code, now = code.replace(" ", ""), int(time.time() // 30)
        with self.lock:
            for step in (now - 1, now, now + 1):
                if step > self.last_totp_step and hmac.compare_digest(totp_code(self.totp_secret, step), code):
                    self.last_totp_step = step
                    return True
        return False

    def blocked_for(self, ip):
        """Secondes de blocage restantes pour cette IP (0 si elle peut essayer)."""
        with self.lock:
            recent = [t for t in self.failures.get(ip, []) if time.time() - t < self.BLOCK_SECONDS]
            self.failures[ip] = recent
            return int(self.BLOCK_SECONDS - (time.time() - recent[0])) if len(recent) >= self.MAX_FAILURES else 0

    def record(self, ip, ok):
        with self.lock:
            if ok:
                self.failures.pop(ip, None)
            else:
                self.failures.setdefault(ip, []).append(time.time())

    def sign(self, expiry):
        return hmac.new(self.secret, f"{expiry}|{self.fingerprint}".encode(), hashlib.sha256).hexdigest()

    def cookie(self, remember, secure):
        expiry = int(time.time()) + (self.REMEMBER_SECONDS if remember else self.SESSION_SECONDS)
        attrs = ["Path=/", "HttpOnly", "SameSite=Lax"] + (["Secure"] if secure else []) + \
            ([f"Max-Age={self.REMEMBER_SECONDS}"] if remember else [])
        return "; ".join([f"{self.COOKIE}={expiry}.{self.sign(expiry)}"] + attrs)

    def clear_cookie(self, secure):
        return "; ".join([f"{self.COOKIE}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"] +
                         (["Secure"] if secure else []))

    def expiry(self, cookie_header):
        """Échéance de la session portée par le cookie, ou 0 si absente, expirée ou mal signée."""
        try:
            morsel = SimpleCookie(cookie_header or "").get(self.COOKIE)
        except CookieError:
            return 0
        expiry, _, sig = (morsel.value if morsel else "").partition(".")
        ok = expiry.isdigit() and int(expiry) > time.time() and hmac.compare_digest(sig, self.sign(int(expiry)))
        return int(expiry) if ok else 0

    def valid(self, cookie_header):
        return bool(self.expiry(cookie_header))

    def renewal(self, cookie_header, secure):
        """Cookie neuf de 30 jours pour une session « se souvenir » entamée depuis plus de RENEW_AFTER
        (les sessions courtes de 12 h ne sont jamais prolongées) ; None s'il n'y a rien à renouveler."""
        left = self.expiry(cookie_header) - time.time()
        if self.SESSION_SECONDS < left < self.REMEMBER_SECONDS - self.RENEW_AFTER:
            return self.cookie(True, secure)
        return None


TOTP_FIELD = """<label>Code de l'application d'authentification
    <input type="text" name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6"
           placeholder="123456" required>
  </label>"""


def login_page(message="", totp=False):
    with open(os.path.join(STATIC_DIR, "login.html"), encoding="utf-8") as f:
        html = f.read()
    if APP_LABEL:  # PREPROD : logo marqué « TEST »
        html = html.replace('class="brand" src="static/favicon.svg"',
                            'class="brand test" src="static/favicon-test.svg"')
    esc = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return html.replace("{{message}}", f'<p class="error">{esc}</p>' if message else "") \
        .replace("{{totp}}", TOTP_FIELD if totp else "").encode()


def make_handler(mirror, season, fuel, history, offers, battery_kwh, health, car, curves, gate):
    class Handler(BaseHTTPRequestHandler):
        server_version = "e208-dashboard"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def send(self, code, body, ctype, headers=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in {**SECURITY_HEADERS, **(headers or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def redirect(self, location, cookie=None):
            self.send(303, b"", "text/plain", {"Location": location, **({"Set-Cookie": cookie} if cookie else {})})

        def renewed(self):
            """En-tête Set-Cookie prolongeant la session « se souvenir » (PWA : pas de reconnexion à chaque lancement)."""
            addr = self.client_address[0]
            if not gate.required(addr, self.headers):
                return None
            cookie = gate.renewal(self.headers.get("Cookie"), gate.via_proxy(addr))
            return {"Set-Cookie": cookie} if cookie else None

        def to_https(self):
            """Arrivée en HTTP via le proxy : redirection permanente vers la même adresse en HTTPS. Refus de toute
            requête venue du proxy tant qu'aucun mot de passe n'est configuré (la réponse est alors envoyée ici)."""
            if gate.exposed_without_login(self.client_address[0], self.headers):
                log.warning("requête Internet refusée : mire non configurée (DASHBOARD_PASSWORD_HASH vide)")
                self.send(403, "Accès distant désactivé : définir DASHBOARD_PASSWORD_HASH (voir README).".encode(),
                          "text/plain; charset=utf-8")
                return True
            host = (self.headers.get("Host") or "").split(":")[0]
            if not host or not gate.insecure(self.client_address[0], self.headers):
                return False
            self.send(301, b"", "text/plain", {"Location": f"https://{host}{self.path}"})
            return True

        def do_POST(self):  # noqa: N802
            addr = self.client_address[0]
            if self.to_https():
                return
            if self.path.split("?")[0] != "/login" or not gate.required(addr, self.headers):
                self.send(404, b"not found", "text/plain")
                return
            ip = gate.client_ip(addr, self.headers)
            wait = gate.blocked_for(ip)
            if wait:
                self.send(429, login_page(f"Trop d'essais : réessaie dans {wait // 60 + 1} min.", bool(gate.totp_secret)), "text/html; charset=utf-8")
                return
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(min(length, 4096)).decode(errors="replace")) if length <= 4096 else {}
            # le code n'est vérifié (et consommé) qu'avec le bon mot de passe
            ok = gate.check(form.get("password", [""])[0]) and gate.check_code(form.get("code", [""])[0])
            gate.record(ip, ok)
            if ok:
                log.info("connexion réussie depuis %s", ip)
                self.redirect("/", gate.cookie(form.get("remember") == ["1"], gate.via_proxy(addr)))
            else:
                log.warning("mot de passe%s refusé depuis %s", " ou code" if gate.totp_secret else "", ip)
                time.sleep(1)
                self.send(401, login_page("Mot de passe ou code incorrect." if gate.totp_secret else "Mot de passe incorrect.",
                                               bool(gate.totp_secret)), "text/html; charset=utf-8")

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            addr = self.client_address[0]
            if self.to_https():
                return
            authed = not gate.required(addr, self.headers) or gate.valid(self.headers.get("Cookie"))
            if path == "/login":
                if authed:
                    self.redirect("/")
                else:
                    self.send(200, login_page(totp=bool(gate.totp_secret)), "text/html; charset=utf-8")
            elif path == "/logout":
                self.redirect("/login", gate.clear_cookie(gate.via_proxy(addr)))
            elif not authed and path not in PUBLIC_PATHS:
                if path.startswith("/api/"):
                    self.send(401, b'{"error": "connexion requise"}', "application/json")
                else:
                    self.redirect("/login")
            elif path == "/sw.js":
                # servi à la racine pour que le service worker couvre tout le site
                with open(os.path.join(STATIC_DIR, "sw.js"), "rb") as f:
                    self.send(200, f.read(), STATIC_TYPES[".js"])
            elif path.startswith("/static/"):
                file = os.path.realpath(os.path.join(STATIC_DIR, path[len("/static/"):]))
                ctype = STATIC_TYPES.get(os.path.splitext(file)[1])
                if file.startswith(STATIC_DIR + os.sep) and ctype and os.path.isfile(file):
                    with open(file, "rb") as f:
                        body = f.read()
                    if APP_LABEL and path == "/static/manifest.webmanifest":
                        manifest = json.loads(body)
                        manifest["name"] += f" {APP_LABEL}"
                        manifest["short_name"] += f" {APP_LABEL}"
                        for icon in manifest["icons"]:
                            icon["src"] = icon["src"].replace(".png", "-test.png")
                        body = json.dumps(manifest, ensure_ascii=False).encode()
                    self.send(200, body, ctype)
                else:
                    self.send(404, b"not found", "text/plain")
            elif path in ("/", "/index.html"):
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    self.send(200, f.read(), "text/html; charset=utf-8", self.renewed())
            elif path == "/api/data":
                try:
                    force = "refresh=1" in self.path
                    sheet = mirror.get(force=force)
                    car_state, charge_curves = car.get(force=force), curves.get(force=force)
                    data = dict(sheet, season=season.get(force=force), battery_kwh=battery_kwh,
                                health=health.get(force=force), car=car_state, charge_curves=charge_curves,
                                charge_estimate=charge_estimate(car_state, charge_curves),
                                offers=dict(offers, fuel=fuel.get() if offers["petrol_l_100km"] else None),
                                # prix du carburant le jour de chaque recharge (équivalent essence)
                                fuel_history=history.get({str(r[1])[:10] for r in sheet["charges"]["rows"]})
                                if offers["petrol_l_100km"] else {},
                                auth=gate.required(addr, self.headers))
                    self.send(200, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8",
                              self.renewed())
                except Exception as exc:  # pylint: disable=broad-except
                    log.exception("lecture du Sheet impossible")
                    self.send(502, json.dumps({"error": str(exc)}).encode(), "application/json")
            elif path == "/healthz":
                self.send(200, b"ok", "text/plain")
            else:
                self.send(404, b"not found", "text/plain")

    return Handler


def prompt_hash():
    """--hash-password : demande le mot de passe (sans l'afficher) et imprime la ligne à mettre dans .env."""
    password = getpass.getpass("Mot de passe du dashboard (12 caractères minimum) : ")
    if len(password) < 12:
        sys.exit("Trop court : 12 caractères minimum.")
    if getpass.getpass("Confirmation : ") != password:
        sys.exit("Les deux saisies diffèrent.")
    print(f"DASHBOARD_PASSWORD_HASH={hash_password(password)}")


def setup_totp():
    """--setup-totp : génère une clé TOTP, affiche son QR code (sur stderr), vérifie un code puis imprime la ligne
    à mettre dans .env (sur stdout, seule : elle peut être capturée par la commande appelante)."""
    import segno  # pylint: disable=import-outside-toplevel  (utile seulement ici)
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    label = "Dashboard e-208"
    uri = f"otpauth://totp/{quote(label)}?secret={secret}&issuer={quote(label)}&digits=6&period=30&algorithm=SHA1"
    err = sys.stderr
    err.write("\nScanne ce QR code avec l'application d'authentification :\n")
    segno.make(uri, error="m").terminal(out=err, compact=True)
    err.write(f"\nOu saisis la clé à la main : {' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))}\n\n")
    for _ in range(3):
        err.write("Code affiché par l'application (6 chiffres) : ")
        err.flush()
        code = sys.stdin.readline().strip().replace(" ", "")
        now = int(time.time() // 30)
        if any(hmac.compare_digest(totp_code(secret, s), code) for s in (now - 1, now, now + 1)):
            err.write("Code correct.\n")
            print(f"DASHBOARD_TOTP_SECRET={secret}")
            return
        err.write("Code incorrect, réessaie.\n")
    sys.exit("Trois codes incorrects : vérifie l'heure du téléphone et relance la commande.")


def main():
    if "--hash-password" in sys.argv:
        prompt_hash()
        return
    if "--setup-totp" in sys.argv:
        setup_totp()
        return
    logging.basicConfig(level=logging.DEBUG if os.environ.get("DEBUG") else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    spreadsheet_id = os.environ.get("SPREADSHEET_ID") or sys.exit("SPREADSHEET_ID manquant")
    mirror = SheetMirror(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/google-sa.json"),
                         spreadsheet_id, int(os.environ.get("CACHE_SECONDS", "300")))
    season = TempoSeason(os.environ.get("TEMPO_API_URL", "https://www.api-couleur-tempo.fr/api"))
    fuel = FuelPrice(os.environ.get("PETROL_FUEL", "sp98"), os.environ.get("PETROL_DEPARTEMENT", "").strip())
    history = FuelHistory(os.environ.get("PETROL_FUEL", "sp98"), os.environ.get("PETROL_DEPARTEMENT", "").strip(),
                          os.environ.get("DATA_DIR", "").strip())
    offers = offers_config()
    psacc = Psacc(os.environ.get("PSACC_URL", "http://psacc:5000"), os.environ.get("VIN") or None)
    health, car = BatteryHealth(psacc), CarStatus(psacc)
    curves = ChargeCurves(psacc, os.environ.get("PSACC_CONFIG", "/config"))
    gate = PasswordGate(os.environ.get("DASHBOARD_PASSWORD_HASH", "").strip(), os.environ.get("SESSION_SECRET", "").strip(),
                        os.environ.get("TRUSTED_PROXY", "").strip(), os.environ.get("LOGIN_SCOPE", "proxy").strip(),
                        os.environ.get("DASHBOARD_TOTP_SECRET", "").strip())
    if gate.enabled:
        scope = "toutes les requêtes" if gate.scope == "all" or not gate.trusted_proxy \
            else f"requêtes venant du proxy {gate.trusted_proxy}"
        log.info("mire de connexion active%s (%s)", " avec code TOTP" if gate.totp_secret else "", scope)
    port = int(os.environ.get("PORT", "8080"))
    log.info("front e-208 sur le port %d (comparatif : %s)", port, json.dumps(offers))
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(mirror, season, fuel, history, offers, env_price("BATTERY_KWH"), health, car, curves, gate)).serve_forever()


if __name__ == "__main__":
    main()
