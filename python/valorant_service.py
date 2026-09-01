# valorant_service.py
"""
ValorantDataService — couche d'accès aux données Valorant / Riot.

Responsabilités séparées (comme demandé) :
  * get_current_session()  -> état local (Riot lockfile + presences) :
    state / map / agent / score / mode.
  * get_current_match()    -> meilleure représentation disponible de la
    partie en cours.
  * get_match_details()    -> à étendre avec API Henrik/riot-api si token
    fourni (non implémenté par défaut, retourne N/A).
  * get_match_history()    -> à étendre ; pour l'instant l'historique vient
    de la base SQLite locale.
  * get_player_stats()     -> stats agrégées depuis la base locale.

Le monitor n'importe PAS ce module directement ; il consomme le résultat
par session.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import config
import logger

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    REQUESTS_OK = True
except Exception:
    REQUESTS_OK = False

try:
    import psutil
except Exception:
    psutil = None


LOCKFILE_PATH = os.path.expandvars(r"%LOCALAPPDATA%\Riot Games\Riot Client\Config\lockfile")


# --- Mappages --------------------------------------------------------------
MAP_MAPPING = {
    "/Game/Maps/Duality/Duality": "Bind",
    "/Game/Maps/Triad/Triad": "Haven",
    "/Game/Maps/Bonsai/Bonsai": "Split",
    "/Game/Maps/Ascent/Ascent": "Ascent",
    "/Game/Maps/Port/Port": "Breeze",
    "/Game/Maps/Canyon/Canyon": "Fracture",
    "/Game/Maps/Pitt/Pitt": "Pearl",
    "/Game/Maps/Jam/Jam": "Lotus",
    "/Game/Maps/Sunset/Sunset": "Sunset",
    "/Game/Maps/Burrow/Burrow": "Abyss",
}

AGENT_MAPPING = {
    "Clay": "Raze", "Vampire": "Reyna", "Wraith": "Omen", "Grizzly": "Breach",
    "Hunter": "Sova", "Rift": "Astra", "Phoenix": "Phoenix", "Sarge": "Brimstone",
    "Ninja": "Jett", "Gumshoe": "Cypher", "BountyHunter": "Fade", "Aggro": "Gekko",
    "Nouveau": "Iso", "Mage": "Harbor", "Sprinter": "Neon", "Deadeye": "Chamber",
    "Seis": "Sage", "Thief": "Yoru", "Cable": "Deadlock", "Chronovoid": "Clove",
    "Sentry": "Killjoy", "Thorn": "Vyse", "Teatime": "Tejo",
}

# sessionLoopState -> catégorie lisible. Les états "matchables" sont
# ceux où l'enregistrement peut être pertinent.
STATE_LABELS = {
    "MENUS": "Menu",
    "PREGAME": "Agent select",
    "INGAME": "En partie",
    "GAMEMODE": "Mode spécial",
    "SPECTATING": "Spectateur",
}

# États qu'on considère comme une "vraie partie" enregistrable.
# "INGAME" est la valeur retournée par le client Riot une fois le round
# commencé. "GAMEMODE" couvre Team Deathmatch/Escalation/Spike Rush/etc.
RECORDABLE_STATES = {"INGAME", "GAMEMODE"}


class RiotClientNotRunning(Exception):
    pass


@dataclass
class SessionState:
    state: str = "MENUS"
    state_label: str = "Menu"
    map: str = "Inconnu"
    agent: str = "Inconnu"
    score: str = "0-0"
    ally_score: int = 0
    enemy_score: int = 0
    queue_id: Optional[str] = None
    party_size: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --- Service ---------------------------------------------------------------
class ValorantDataService:
    def __init__(self):
        self._lock = threading.RLock()
        self._cached_session: Optional[SessionState] = None
        self._cached_session_ts: float = 0.0
        self._cache_ttl: float = 1.0

    # ----- process detection -----
    @staticmethod
    def is_valorant_running() -> bool:
        return _any_process_named(config.VALORANT_PROCESS_NAMES)

    @staticmethod
    def is_riot_running() -> bool:
        return _any_process_named(config.RIOT_PROCESS_NAMES)

    @staticmethod
    def riot_lockfile_present() -> bool:
        try:
            return os.path.exists(LOCKFILE_PATH)
        except Exception:
            return False

    # ----- session -----
    def get_current_session(self, use_cache: bool = True) -> SessionState:
        with self._lock:
            now = time.time()
            if (use_cache and self._cached_session is not None
                    and (now - self._cached_session_ts) < self._cache_ttl):
                return self._cached_session
            try:
                state = self._fetch_session()
            except RiotClientNotRunning:
                state = SessionState()
            self._cached_session = state
            self._cached_session_ts = now
            return state

    def invalidate_cache(self):
        with self._lock:
            self._cached_session = None

    def get_current_match(self) -> Dict[str, Any]:
        s = self.get_current_session()
        return {
            "in_match": s.state in RECORDABLE_STATES,
            "state": s.state,
            "state_label": s.state_label,
            "map": s.map,
            "agent": s.agent,
            "score": s.score,
            "queue_id": s.queue_id,
        }

    # ----- historique (à enrichir avec API Henrik si token dispo) -----
    def get_match_history(self, count: int = 20) -> List[Dict[str, Any]]:
        return []  # l'UI consomme l'historique via la base locale

    def get_match_details(self, match_id: str) -> Dict[str, Any]:
        return {"match_id": match_id, "note": "Détails distants non disponibles."}

    def get_player_stats(self) -> Dict[str, Any]:
        return {"note": "Stats distantes non disponibles sans token API."}

    # ----- internals -----
    def _fetch_session(self) -> SessionState:
        if not REQUESTS_OK:
            raise RiotClientNotRunning("Module 'requests' indisponible")
        creds = self._read_lockfile()
        puuid = self._get_own_puuid(creds["port"], creds["password"])
        presences = self._get_presences(creds["port"], creds["password"])
        for p in presences:
            if p.get("puuid") == puuid and p.get("product") == "valorant":
                private_raw = p.get("private")
                if not private_raw:
                    return SessionState()
                try:
                    info = json.loads(base64.b64decode(private_raw))
                except Exception:
                    return SessionState()
                match_data = info.get("matchPresenceData") or {}
                state = match_data.get("sessionLoopState") or "MENUS"
                raw_map = info.get("matchMap")
                map_name = MAP_MAPPING.get(raw_map, "Inconnu")
                agent_path = info.get("characterCharacter") or ""
                agent_name = "Inconnu"
                if agent_path:
                    parts = agent_path.split('/')
                    if len(parts) > 3:
                        agent_name = AGENT_MAPPING.get(parts[3], parts[3])
                ally = int(info.get("partyOwnerMatchScoreAllyTeam") or 0)
                enemy = int(info.get("partyOwnerMatchScoreEnemyTeam") or 0)
                party_size = int(info.get("partySize") or 0)
                queue_id = info.get("queueId") or match_data.get("queueId")
                return SessionState(
                    state=state,
                    state_label=STATE_LABELS.get(state, state),
                    map=map_name,
                    agent=agent_name,
                    score=f"{ally}-{enemy}",
                    ally_score=ally,
                    enemy_score=enemy,
                    queue_id=str(queue_id) if queue_id else None,
                    party_size=party_size,
                    raw=info,
                )
        return SessionState()

    def _read_lockfile(self) -> Dict[str, str]:
        if not os.path.exists(LOCKFILE_PATH):
            raise RiotClientNotRunning("Riot Client fermé")
        try:
            with open(LOCKFILE_PATH, "r", encoding="utf-8") as f:
                parts = f.read().strip().split(":")
        except Exception:
            raise RiotClientNotRunning("lockfile illisible")
        if len(parts) != 5:
            raise RiotClientNotRunning("lockfile invalide")
        return {"port": parts[2], "password": parts[3]}

    def _get_own_puuid(self, port: str, password: str) -> str:
        url = f"https://127.0.0.1:{port}/chat/v1/session"
        r = requests.get(
            url,
            headers={"Authorization": f"Basic {base64.b64encode(f'riot:{password}'.encode()).decode()}"},
            verify=False,
            timeout=3,
        )
        r.raise_for_status()
        return r.json()["puuid"]

    def _get_presences(self, port: str, password: str) -> List[Dict[str, Any]]:
        url = f"https://127.0.0.1:{port}/chat/v4/presences"
        r = requests.get(
            url,
            headers={"Authorization": f"Basic {base64.b64encode(f'riot:{password}'.encode()).decode()}"},
            verify=False,
            timeout=3,
        )
        r.raise_for_status()
        return r.json().get("presences", [])


def _any_process_named(names) -> bool:
    if psutil is None:
        try:
            out = subprocess_output("tasklist")  # noqa
        except Exception:
            return False
        low = out.lower()
        return any(n.lower() in low for n in names)
    try:
        wanted = {n.lower() for n in names}
        for proc in psutil.process_iter(["name"]):
            if (proc.info.get("name") or "").lower() in wanted:
                return True
    except Exception:
        return False
    return False


def subprocess_output(cmd: str) -> str:
    import subprocess
    return subprocess.check_output(cmd, shell=True).decode(errors="ignore")
