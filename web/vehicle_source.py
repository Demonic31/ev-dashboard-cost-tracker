"""Interface entre le dashboard et la source de données d'une marque.

Chaque marque fournit brands/<marque>/source.py, qui définit create(env) -> VehicleSource. Le dashboard ne connaît
que ce contrat : il ne sait rien de l'outil qui parle à la voiture (psacc pour Stellantis, etc.).

Chaque méthode accepte force=True (relire sans attendre le cache) et renvoie :
  * un dict de données (format décrit ci-dessous) ;
  * {"error": "..."} si la donnée est momentanément indisponible (affiché comme tel) ;
  * None si la marque ne fournit pas cette donnée (le bloc correspondant est masqué).
"""
import importlib.util
import logging
import os
import re
import threading
import time

log = logging.getLogger("ev-dashboard")

# Vocabulaire commun des états de charge (VehicleSource.car()["status"])
CHARGE_STATUSES = ("charging", "finished", "stopped", "failure", "disconnected")


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


class VehicleSource:
    """Contrat d'une source de marque. Par défaut, rien n'est fourni (None)."""

    def name(self, force=False):
        """{"name": "Marque Modèle"} affiché en titre (ex. « Peugeot e-208 »)."""
        return None

    def car(self, force=False):
        """Dernier état connu, sans réveiller la voiture : {"level" (%), "autonomy" (km), "updated_at" (ISO 8601),
        "status" (un de CHARGE_STATUSES, ou valeur brute inconnue), "plugged", "charging_rate" (km/h),
        "remaining_hours" (fin de charge annoncée par la voiture), "mileage" (km), "mileage_at"}."""
        return None

    def health(self, force=False):
        """Santé de la batterie : {"soh" (%), "fetched_at", "note" (origine de l'indicateur, affichée)}."""
        return None

    def curves(self, force=False):
        """Courbes de charge : {"curves": {ID de recharge du Sheet: [[ISO, niveau, autonomie], ...]},
        "ongoing": {"start_at", "points": [[ISO, niveau], ...]} ou None, "median_rate" (%/h des dernières recharges)
        ou None, "median_count"}."""
        return None


def load_source(brand, brands_dir, env):
    """Source de la marque `brand` (brands/<brand>/source.py)."""
    if not re.fullmatch(r"[a-z0-9_-]+", brand or ""):
        raise ValueError(f"BRAND invalide : {brand!r}")
    path = os.path.join(brands_dir, brand, "source.py")
    if not os.path.isfile(path):
        raise ValueError(f"marque inconnue : {brand} ({path} absent)")
    spec = importlib.util.spec_from_file_location(f"brand_{brand.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = module.create(env)
    if not isinstance(source, VehicleSource):
        raise TypeError(f"{path} : create() doit renvoyer une VehicleSource")
    log.info("source de données : %s", brand)
    return source
