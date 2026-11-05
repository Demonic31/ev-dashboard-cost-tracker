"""Source Stellantis (Peugeot, Citroën, DS, Opel, Vauxhall) pour le dashboard : psa_car_controller (psacc).

Lecture seule : API web de psacc par le réseau Docker interne (aucun appel aux serveurs Stellantis, la voiture
n'est jamais réveillée) et copie de sa base SQLite (jamais verrouillée ni modifiée).
"""
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone

import requests

from vehicle_source import VehicleSource, cached

log = logging.getLogger("ev-dashboard")

# États de charge de l'API Stellantis -> vocabulaire commun (vehicle_source.CHARGE_STATUSES)
STATUSES = {"InProgress": "charging", "Finished": "finished", "Stopped": "stopped", "Failure": "failure",
            "Disconnected": "disconnected"}


def iso_hours(duration):
    """Durée ISO 8601 de l'API Stellantis (« PT2H25M ») en heures, None si absente ou illisible."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", duration or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, sec = (float(g or 0) for g in m.groups())
    return 24 * d + h + mi / 60 + sec / 3600


def iso(ts):
    return datetime.fromisoformat(ts).astimezone(timezone.utc).isoformat(timespec="seconds")


def failure(what, exc):
    log.warning("%s indisponible : %s", what, exc)
    return {"error": str(exc) or type(exc).__name__}


class Psacc:
    """Client de l'API web de psacc. VIN imposé, sinon premier véhicule électrique du compte."""

    def __init__(self, url, vin):
        self.url, self._vin = url.rstrip("/"), vin

    def vin(self):
        if not self._vin:
            self._vin = next(c["vin"] for c in self.get("/get_vehicles") if (c.get("battery_power") or 0) > 0)
        return self._vin

    def get(self, path):
        return requests.get(f"{self.url}{path}", timeout=15).json()


class StellantisSource(VehicleSource):
    ERRORS = (requests.RequestException, ValueError, StopIteration)

    def __init__(self, url, vin, config_dir):
        self.psacc, self.config_dir = Psacc(url, vin), config_dir

    def credit(self):
        return {"group": "Stellantis", "name": "psa_car_controller", "url": "https://github.com/flobz/psa_car_controller",
                "provides": "état de la voiture, santé de la batterie (SOH), courbes de charge, recharges et trajets"}

    @cached(3600)
    def name(self):
        try:
            vin = self.psacc.vin()
            car = next(c for c in self.psacc.get("/get_vehicles") if c.get("vin") == vin)
            name = " ".join(str(car.get(k) or "").strip() for k in ("brand", "label")).strip()
            return {"name": name} if name else {"error": "marque et modèle inconnus de psacc"}
        except self.ERRORS as exc:
            return failure("marque et modèle", exc)

    @cached(3600)
    def health(self):
        """SOH relevé par psacc : champ « resistance » de l'API Stellantis."""
        try:
            soh = self.psacc.get(f"/battery/soh/{self.psacc.vin()}").get("soh")
            if soh is None:
                return {"error": "aucun SOH relevé par psacc"}
            return {"soh": soh, "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "note": "indicateur Stellantis (résistance interne) relevé par psacc"}
        except self.ERRORS as exc:
            return failure("SOH", exc)

    @cached(300)
    def car(self):
        """Lu dans le cache de psacc (from_cache=1) : valeurs du dernier envoi de la voiture."""
        try:
            info = self.psacc.get(f"/get_vehicleinfo/{self.psacc.vin()}?from_cache=1")
            elec = next(e for e in info.get("energy") or [] if e.get("type") == "Electric")
            charging = elec.get("charging") or {}
            odo = info.get("timed_odometer") or {}
            status = charging.get("status")
            return {"level": elec.get("level"), "autonomy": elec.get("autonomy"), "updated_at": elec.get("updated_at"),
                    "status": STATUSES.get(status, status), "plugged": charging.get("plugged"),
                    "charging_rate": charging.get("charging_rate"),
                    "remaining_hours": iso_hours(charging.get("remaining_time")),
                    "mileage": odo.get("mileage"), "mileage_at": odo.get("updated_at")}
        except self.ERRORS as exc:
            return failure("état de la voiture", exc)

    @cached(300)
    def curves(self):
        """Table battery_curve de psacc, sur une copie de la base (avec son WAL)."""
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
                        points = [[iso(d), lvl, aut] for d, lvl, aut in conn.execute(
                            "SELECT date, level, autonomy FROM battery_curve WHERE VIN=? AND start_at=? ORDER BY date",
                            (vin, start)).fetchall()]
                        # début et fin de la table battery : bornes réelles de la charge
                        if start_level is not None and (not points or points[0][0] > iso(start)):
                            points.insert(0, [iso(start), start_level, None])
                        if end_level is not None and (not points or points[-1][0] < iso(stop)):
                            points.append([iso(stop), end_level, None])
                        sid = "C-" + datetime.fromisoformat(start).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        curves[sid] = points
                    ongoing = self.ongoing(conn, vin)
                finally:
                    conn.close()
            rate, count = self.median_rate(charges)
            return {"curves": curves, "ongoing": ongoing, "median_rate": rate, "median_count": count}
        except (OSError, sqlite3.Error, *self.ERRORS) as exc:
            return failure("courbes de charge", exc)

    @staticmethod
    def ongoing(conn, vin):
        """Points de la charge en cours (ligne de battery sans stop_at), début de charge compris."""
        row = conn.execute("SELECT start_at, start_level FROM battery WHERE VIN=? AND stop_at IS NULL "
                           "ORDER BY start_at DESC LIMIT 1", (vin,)).fetchone()
        if not row:
            return None
        start, start_level = row
        points = [[iso(d), lvl] for d, lvl in conn.execute(
            "SELECT date, level FROM battery_curve WHERE VIN=? AND start_at=? ORDER BY date", (vin, start)).fetchall()
            if lvl is not None]
        if start_level is not None and (not points or points[0][0] > iso(start)):
            points.insert(0, [iso(start), start_level])
        return {"start_at": iso(start), "points": points}

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


def create(env):
    return StellantisSource(env.get("PSACC_URL") or "http://psacc:5000", env.get("VIN") or None,
                            env.get("PSACC_CONFIG") or "/config")
