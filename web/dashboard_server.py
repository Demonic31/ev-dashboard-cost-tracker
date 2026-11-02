#!/usr/bin/env python3
"""Front web en lecture seule du Google Sheet de suivi e-208 / Tempo.

Sert une page HTML et un endpoint /api/data qui renvoie, en JSON, les valeurs calculées par le Sheet
(onglets Dashboard, Recharges, Trajets, Couleurs Tempo). Aucune donnée n'est recalculée ici : la page est un
miroir du Sheet, rafraîchi au plus toutes les CACHE_SECONDS.

En complément : la saison Tempo en cours (api-couleur-tempo.fr, comme la synchro) et les paramètres du comparatif
des offres (prix des autres options EDF, prix moyen du carburant sur data.economie.gouv.fr).

Mire de connexion facultative : un mot de passe unique dont l'empreinte (scrypt) est fournie au conteneur
(DASHBOARD_PASSWORD_HASH, générée avec --hash-password).
"""
import getpass
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

SHEET_EPOCH = datetime(1899, 12, 30)
COLORS = {1: "BLEU", 2: "BLANC", 3: "ROUGE"}
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
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
                finally:
                    conn.close()
            return {"curves": curves}
        except (OSError, sqlite3.Error, requests.RequestException, ValueError, StopIteration) as exc:
            log.warning("courbes de charge indisponibles : %s", exc)
            return {"error": str(exc) or type(exc).__name__}


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


def hash_password(password, n=2 ** 15, r=8, p=1):
    """Empreinte scrypt « scrypt:n:r:p:sel:clé » (hexadécimal, sans « $ » pour rester lisible par Compose)."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt:{n}:{r}:{p}:{salt.hex()}:{key.hex()}"


class PasswordGate:
    """Mire de connexion à mot de passe unique.

    Désactivée si aucune empreinte n'est fournie. Sinon, la connexion est exigée pour les requêtes qui passent par
    le reverse proxy (TRUSTED_PROXY), ou pour toutes si LOGIN_SCOPE=all ou si le proxy n'est pas renseigné.
    La session est un cookie signé (HMAC) qui porte son expiration ; changer le mot de passe invalide les sessions.
    Après MAX_FAILURES échecs en BLOCK_SECONDS, l'IP du visiteur (X-Real-IP posé par le proxy) est bloquée."""

    COOKIE = "e208_session"
    MAX_FAILURES, BLOCK_SECONDS = 5, 15 * 60
    SESSION_SECONDS, REMEMBER_SECONDS = 12 * 3600, 30 * 86400

    def __init__(self, password_hash, secret, trusted_proxy, scope):
        self.enabled = bool(password_hash)
        self.trusted_proxy, self.scope = trusted_proxy, scope
        self.failures, self.lock = {}, threading.Lock()
        if not self.enabled:
            return
        _, n, r, p, salt, key = password_hash.split(":")
        self.params, self.salt, self.key = (int(n), int(r), int(p)), bytes.fromhex(salt), bytes.fromhex(key)
        if not secret:
            log.warning("SESSION_SECRET absent : clé aléatoire, les sessions ne survivront pas au redémarrage")
        self.secret = (secret or secrets.token_hex(32)).encode()
        self.fingerprint = hashlib.sha256(password_hash.encode()).hexdigest()[:16]

    def via_proxy(self, addr):
        return bool(self.trusted_proxy) and addr == self.trusted_proxy

    def client_ip(self, addr, headers):
        return (headers.get("X-Real-IP") or addr).strip() if self.via_proxy(addr) else addr

    def insecure(self, addr, headers):
        """Requête arrivée en HTTP sur le proxy (X-Forwarded-Proto posé par le Synology) : à rediriger en HTTPS."""
        return self.via_proxy(addr) and (headers.get("X-Forwarded-Proto") or "").lower() == "http"

    def required(self, addr):
        return self.enabled and (self.scope == "all" or not self.trusted_proxy or self.via_proxy(addr))

    def check(self, password):
        n, r, p = self.params
        key = hashlib.scrypt(password.encode(), salt=self.salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024,
                             dklen=len(self.key))
        return hmac.compare_digest(key, self.key)

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
        attrs = ["Path=/", "HttpOnly", "SameSite=Strict"] + (["Secure"] if secure else []) + \
            ([f"Max-Age={self.REMEMBER_SECONDS}"] if remember else [])
        return "; ".join([f"{self.COOKIE}={expiry}.{self.sign(expiry)}"] + attrs)

    def clear_cookie(self, secure):
        return "; ".join([f"{self.COOKIE}=", "Path=/", "HttpOnly", "SameSite=Strict", "Max-Age=0"] +
                         (["Secure"] if secure else []))

    def valid(self, cookie_header):
        try:
            morsel = SimpleCookie(cookie_header or "").get(self.COOKIE)
        except CookieError:
            return False
        expiry, _, sig = (morsel.value if morsel else "").partition(".")
        return expiry.isdigit() and int(expiry) > time.time() and hmac.compare_digest(sig, self.sign(int(expiry)))


def login_page(message=""):
    with open(os.path.join(STATIC_DIR, "login.html"), encoding="utf-8") as f:
        html = f.read()
    esc = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return html.replace("{{message}}", f'<p class="error">{esc}</p>' if message else "").encode()


def make_handler(mirror, season, fuel, offers, battery_kwh, health, car, curves, gate):
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

        def to_https(self):
            """Arrivée en HTTP via le proxy : redirection permanente vers la même adresse en HTTPS."""
            host = (self.headers.get("Host") or "").split(":")[0]
            if not host or not gate.insecure(self.client_address[0], self.headers):
                return False
            self.send(301, b"", "text/plain", {"Location": f"https://{host}{self.path}"})
            return True

        def do_POST(self):  # noqa: N802
            addr = self.client_address[0]
            if self.to_https():
                return
            if self.path.split("?")[0] != "/login" or not gate.required(addr):
                self.send(404, b"not found", "text/plain")
                return
            ip = gate.client_ip(addr, self.headers)
            wait = gate.blocked_for(ip)
            if wait:
                self.send(429, login_page(f"Trop d'essais : réessaie dans {wait // 60 + 1} min."), "text/html; charset=utf-8")
                return
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(min(length, 4096)).decode(errors="replace")) if length <= 4096 else {}
            ok = gate.check(form.get("password", [""])[0])
            gate.record(ip, ok)
            if ok:
                log.info("connexion réussie depuis %s", ip)
                self.redirect("/", gate.cookie(form.get("remember") == ["1"], gate.via_proxy(addr)))
            else:
                log.warning("mot de passe refusé depuis %s", ip)
                time.sleep(1)
                self.send(401, login_page("Mot de passe incorrect."), "text/html; charset=utf-8")

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            addr = self.client_address[0]
            if self.to_https():
                return
            authed = not gate.required(addr) or gate.valid(self.headers.get("Cookie"))
            if path == "/login":
                if authed:
                    self.redirect("/")
                else:
                    self.send(200, login_page(), "text/html; charset=utf-8")
            elif path == "/logout":
                self.redirect("/login", gate.clear_cookie(gate.via_proxy(addr)))
            elif not authed and path not in ("/healthz", "/static/theme.js", "/static/vendor/peugeot-logo.svg"):
                if path.startswith("/api/"):
                    self.send(401, b'{"error": "connexion requise"}', "application/json")
                else:
                    self.redirect("/login")
            elif path.startswith("/static/"):
                file = os.path.realpath(os.path.join(STATIC_DIR, path[len("/static/"):]))
                ctype = STATIC_TYPES.get(os.path.splitext(file)[1])
                if file.startswith(STATIC_DIR + os.sep) and ctype and os.path.isfile(file):
                    with open(file, "rb") as f:
                        self.send(200, f.read(), ctype)
                else:
                    self.send(404, b"not found", "text/plain")
            elif path in ("/", "/index.html"):
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    self.send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/api/data":
                try:
                    force = "refresh=1" in self.path
                    data = dict(mirror.get(force=force), season=season.get(force=force), battery_kwh=battery_kwh,
                                health=health.get(force=force), car=car.get(force=force),
                                charge_curves=curves.get(force=force),
                                offers=dict(offers, fuel=fuel.get() if offers["petrol_l_100km"] else None),
                                auth=gate.required(addr))
                    self.send(200, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")
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


def main():
    if "--hash-password" in sys.argv:
        prompt_hash()
        return
    logging.basicConfig(level=logging.DEBUG if os.environ.get("DEBUG") else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    spreadsheet_id = os.environ.get("SPREADSHEET_ID") or sys.exit("SPREADSHEET_ID manquant")
    mirror = SheetMirror(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/google-sa.json"),
                         spreadsheet_id, int(os.environ.get("CACHE_SECONDS", "300")))
    season = TempoSeason(os.environ.get("TEMPO_API_URL", "https://www.api-couleur-tempo.fr/api"))
    fuel = FuelPrice(os.environ.get("PETROL_FUEL", "sp98"), os.environ.get("PETROL_DEPARTEMENT", "").strip())
    offers = offers_config()
    psacc = Psacc(os.environ.get("PSACC_URL", "http://psacc:5000"), os.environ.get("VIN") or None)
    health, car = BatteryHealth(psacc), CarStatus(psacc)
    curves = ChargeCurves(psacc, os.environ.get("PSACC_CONFIG", "/config"))
    gate = PasswordGate(os.environ.get("DASHBOARD_PASSWORD_HASH", "").strip(), os.environ.get("SESSION_SECRET", "").strip(),
                        os.environ.get("TRUSTED_PROXY", "").strip(), os.environ.get("LOGIN_SCOPE", "proxy").strip())
    if gate.enabled:
        log.info("mire de connexion active (%s)", "toutes les requêtes" if gate.scope == "all" or not gate.trusted_proxy
                 else f"requêtes venant du proxy {gate.trusted_proxy}")
    port = int(os.environ.get("PORT", "8080"))
    log.info("front e-208 sur le port %d (comparatif : %s)", port, json.dumps(offers))
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(mirror, season, fuel, offers, env_price("BATTERY_KWH"), health, car, curves, gate)).serve_forever()


if __name__ == "__main__":
    main()
