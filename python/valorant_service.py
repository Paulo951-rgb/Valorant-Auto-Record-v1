# valorant_service.py
"""
ValorantDataService — couche d'accès aux données Valorant / Riot.

Responsabilités :
  * get_current_session()  -> état local (Riot lockfile + presences) :
    state / map / agent / score / mode.
  * get_current_match()    -> représentation de la partie en cours.
  * get_match_details()    -> à étendre via API externe.
  * get_match_history()    -> à étendre.
  * get_player_stats()     -> agrégat local.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import config
import logger

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    REQUESTS_OK = True
    REQUESTS_ERROR: Optional[str] = None
except Exception as e:
    REQUESTS_OK = False
    REQUESTS_ERROR = str(e)

try:
    import psutil
    _PSUTIL_OK = True
except Exception:
    psutil = None
    _PSUTIL_OK = False


LOCKFILE_PATH = os.path.join(
    os.path.expandvars(os.environ.get("LOCALAPPDATA", "")),
    "Riot Games", "Riot Client", "Config", "lockfile"
) if os.name == "nt" else os.path.expanduser(
    "~/.local/share/Riot Games/Riot Client/Config/lockfile"
)


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

# Modes de jeu connus par queueId (best effort, Riot peut changer).
QUEUE_ID_LABELS = {
    "competitive": "Compétitif",
    "unrated": "Non classé",
    "spike_rush": "Spike Rush",
    "deathmatch": "Deathmatch",
    "swiftplay": "Swiftplay",
    "team_deathmatch": "Team Deathmatch",
    "escalation": "Escalation",
    "premier": "Premier",
    "custom": "Personnalisé",
}

STATE_LABELS = {
    "MENUS": "Menu",
    "PREGAME": "Agent select",
    "INGAME": "En partie",
    "GAMEMODE": "Mode spécial",
    "SPECTATING": "Spectateur",
}

# États qu'on considère comme une "vraie partie" enregistrable.
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
    mode: Optional[str] = None
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
        self._cached_processes: Optional[Tuple[bool, bool, bool, float]] = None
        self._processes_ttl: float = 1.0

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
        except OSError:
            return False

    def processes_snapshot(self, use_cache: bool = True) -> Tuple[bool, bool, bool]:
        """Renvoie (valorant, riot, lockfile) avec cache court pour éviter
        plusieurs appels psutil dans le même tick."""
        now = time.time()
        with self._lock:
            if use_cache and self._cached_processes is not None:
                cached, ts = self._cached_processes
                if (now - ts) < self._processes_ttl:
                    return cached
        val = self.is_valorant_running()
        riot = self.is_riot_running()
        lock = self.riot_lockfile_present()
        with self._lock:
            self._cached_processes = ((val, riot, lock), now)
        return val, riot, lock

    # ----- session -----
    def get_current_session(self, use_cache: bool = True) -> SessionState:
        with self._lock:
            now = time.time()
            if (use_cache and self._cached_session is not None
                    and (now - self._cached_session_ts) < self._cache_ttl):
                return self._cached_session
            state = self._fetch_session_safe()
            self._cached_session = state
            self._cached_session_ts = now
            return state

    def invalidate_cache(self):
        with self._lock:
            self._cached_session = None
            self._cached_processes = None

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
            "mode": s.mode,
        }

    # ----- distant / historique -----
    def get_match_history(self, count: int = 20) -> List[Dict[str, Any]]:
        return []

    def get_match_details(self, match_id: str) -> Dict[str, Any]:
        return {"match_id": match_id, "note": "Détails distants non disponibles."}

    def get_player_stats(self) -> Dict[str, Any]:
        return {"note": "Stats distantes non disponibles sans token API."}

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
            "mode": s.mode,
        }

    # ----- internals -----
    def _fetch_session_safe(self) -> SessionState:
        """Comme _fetch_session mais ne lève jamais d'exception."""
        try:
            return self._fetch_session()
        except RiotClientNotRunning:
            return SessionState()
        except Exception as e:
            logger.debug(f"Erreur session Valorant: {e}")
            return SessionState()
        except Exception as e:
            logger.debug(f"Erreur session Valorant: {e}")
            return SessionState()

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
                try:
                    ally = int(info.get("partyOwnerMatchScoreAllyTeam") or 0)
                except (TypeError, ValueError):
                    ally = 0
                try:
                    enemy = int(info.get("partyOwnerMatchScoreEnemyTeam") or 0)
                except (TypeError, ValueError):
                    enemy = 0
                try:
                    party_size = int(info.get("partySize") or 0)
                except (TypeError, ValueError):
                    party_size = 0
                queue_id_raw = info.get("queueId") or match_data.get("queueId")
                queue_id = str(queue_id_raw) if queue_id_raw is not None else None
                mode = QUEUE_ID_LABELS.get(str(queue_id).lower() if queue_id else "",
                                            str(queue_id) if queue_id else None)
                return SessionState(
                    state=state,
                    state_label=STATE_LABELS.get(state, state),
                    map=map_name,
                    agent=agent_name,
                    score=f"{ally}-{enemy}",
                    ally_score=ally,
                    enemy_score=enemy,
                    queue_id=queue_id,
                    mode=mode,
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
        except (OSError, UnicodeDecodeError):
            raise RiotClientNotRunning("lockfile illisible")
        if len(parts) != 5:
            raise RiotClientNotRunning("lockfile invalide")
        return {"port": parts[2], "password": parts[3]}

    def _get_own_puuid(self, port: str, password: str) -> str:
        url = f"https://127.0.0.1:{port}/chat/v1/session"
        try:
            r = requests.get(
                url,
                headers={"Authorization": f"Basic {base64.b64encode(f'riot:{password}'.encode()).decode()}"},
                verify=False,
                timeout=3,
            )
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or "puuid" not in data:
                raise RiotClientNotRunning("Réponse session invalide")
            return data["puuid"]
        except requests.RequestException as e:
            raise RiotClientNotRunning(f"Session Riot injoignable: {e}")

    def _get_presences(self, port: str, password: str) -> List[Dict[str, Any]]:
        url = f"https://127.0.0.1:{port}/chat/v4/presences"
        try:
            r = requests.get(
                url,
                headers={"Authorization": f"Basic {base64.b64encode(f'riot:{password}'.encode()).decode()}"},
                verify=False,
                timeout=3,
            )
            r.raise_for_status()
            data = r.json()
            pres = data.get("presences", []) if isinstance(data, dict) else []
            return [p for p in pres if isinstance(p, dict)]
        except requests.RequestException as e:
            raise RiotClientNotRunning(f"Presences Riot injoignable: {e}")


def _any_process_named(names) -> bool:
    if not _PSUTIL_OK:
        return False
    try:
        wanted = {n.lower() for n in names}
        for proc in psutil.process_iter(["name"]):
            try:
                if (proc.info.get("name") or "").lower() in wanted:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        return False
    return False
