# obs_service.py
"""
OBSService — couche d'abstraction OBS multi-version.

Responsabilités :
  * discover() : cherche toutes les installations d'OBS Studio (Program Files,
    Program Files (x86), chemins personnalisés, processus en cours).
  * launch() : lance OBS discrètement (minimisé, sans voler le focus) puis
    poll la disponibilité du WebSocket avec backoff et timeout.
  * connect() / disconnect() : gère un client ReqClient obsws-python en cache,
    avec reconnexion paresseuse et compatibilité obs-websocket v4 et v5.
  * get_version() : version OBS + version obs-websocket détectées.
  * get_recording_status() / start_recording() / stop_recording()
  * get_recording_path() : récupère le dossier de sortie réellement configuré
    dans OBS.
"""
from __future__ import annotations

import os
import re
import sys
import time
import subprocess
import threading
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple

import config
import logger

try:
    import psutil
except Exception:
    psutil = None

try:
    from obsws_python import ReqClient
    OBSWS_AVAILABLE = True
    OBSWS_IMPORT_ERROR: Optional[str] = None
except Exception as e:  # pragma: no cover
    OBSWS_AVAILABLE = False
    ReqClient = None
    OBSWS_IMPORT_ERROR = str(e)


# =============================================================================
# Modèles de données
# =============================================================================
@dataclass
class ObsInstallation:
    path: str
    version: Optional[str] = None
    running: bool = False
    portable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ObsStatus:
    running: bool = False
    connected: bool = False
    recording: bool = False
    scene: Optional[str] = None
    version: Optional[str] = None
    websocket_version: Optional[int] = None
    obs_exe_path: Optional[str] = None
    output_dir: Optional[str] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# Découverte des installations
# =============================================================================
_VERSION_RE = re.compile(r"obs-studio[-_ ]?(\d+\.\d+(?:\.\d+)?)")


def _read_obs_version(exe_path: str) -> Optional[str]:
    """Lit la version OBS depuis la convention de nommage du dossier parent
    (portable, install) ou de l'exe lui-même. Retourne 'X.Y.Z' ou None."""
    if not exe_path:
        return None
    try:
        for d in (os.path.dirname(exe_path), os.path.dirname(os.path.dirname(exe_path))):
            m = _VERSION_RE.search(os.path.basename(d))
            if m:
                return m.group(1)
        # Fallback : versioninfo Windows via pywin32 absent : on s'arrête là.
    except Exception:
        pass
    return None


def _candidate_paths() -> List[str]:
    """Chemins candidats classiques pour obs64.exe (Windows)."""
    candidates: List[str] = []
    env_vars = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA")
    for var in env_vars:
        base = os.environ.get(var)
        if not base:
            continue
        candidates.extend([
            os.path.join(base, "obs-studio", "bin", "64bit", "obs64.exe"),
            os.path.join(base, "obs-studio", "obs-studio", "bin", "64bit", "obs64.exe"),
        ])
    if sys.platform == "win32":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                candidates.extend([
                    f"{drive}obs-studio\\bin\\64bit\\obs64.exe",
                    f"{drive}Tools\\obs-studio\\bin\\64bit\\obs64.exe",
                    f"{drive}Program Files\\obs-studio\\bin\\64bit\\obs64.exe",
                    f"{drive}Program Files (x86)\\obs-studio\\bin\\64bit\\obs64.exe",
                ])
    else:
        # Sur Linux/Mac, OBS est rarement installé, mais on tente quand même.
        for p in ("/usr/bin/obs", "/usr/local/bin/obs", "/Applications/OBS.app/Contents/MacOS/OBS"):
            if os.path.isfile(p):
                candidates.append(p)
    # Chemins par défaut (Windows).
    candidates.append(r"C:\Program Files\obs-studio\bin\64bit\obs64.exe")
    candidates.append(r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe")
    return candidates


def _running_obs_exes() -> set:
    """Retourne l'ensemble {chemin_absolu, ...} des processus OBS en cours."""
    paths: set = set()
    if psutil is None:
        # tasklist n'existe pas sur Linux/Mac — retourne silencieusement.
        return paths
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            name = (proc.info.get("name") or "").lower()
            if name in ("obs64.exe", "obs.exe"):
                ex = proc.info.get("exe")
                if ex:
                    paths.add(ex.lower())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
    except Exception:
        pass
    return paths


def discover(obs_exe_path: Optional[str] = None) -> List[ObsInstallation]:
    """Cherche toutes les installations OBS détectables. Renvoie une liste
    triée (en cours d'abord) et dédupliquée."""
    found: Dict[str, ObsInstallation] = {}
    running_set = _running_obs_exes()

    # 1) Chemin personnalisé prioritaire.
    if obs_exe_path and os.path.isfile(obs_exe_path):
        key = os.path.normcase(os.path.abspath(obs_exe_path))
        found[key] = ObsInstallation(
            path=obs_exe_path,
            version=_read_obs_version(obs_exe_path),
            running=obs_exe_path.lower() in running_set,
        )

    # 2) Candidats classiques.
    for c in _candidate_paths():
        try:
            if not os.path.isfile(c):
                continue
        except OSError:
            continue
        key = os.path.normcase(os.path.abspath(c))
        if key not in found:
            found[key] = ObsInstallation(
                path=c,
                version=_read_obs_version(c),
                running=c.lower() in running_set,
            )

    # 3) Processus en cours (au cas où le chemin n'est pas dans les listes).
    for p in running_set:
        if not p:
            continue
        try:
            if not os.path.isfile(p):
                continue
        except OSError:
            continue
        key = os.path.normcase(os.path.abspath(p))
        if key not in found:
            found[key] = ObsInstallation(
                path=p,
                version=_read_obs_version(p),
                running=True,
            )

    return sorted(found.values(), key=lambda i: (not i.running, i.path))


def is_obs_process_running() -> bool:
    """Indique si un processus OBS est en cours, sans dépendre du chemin."""
    if psutil is None:
        return False
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in ("obs64.exe", "obs.exe"):
                return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
    except Exception:
        pass
    return False


# =============================================================================
# Service OBS
# =============================================================================
class OBSService:
    """Abstraction OBS unique pour tout le logiciel."""

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None,
                 password: str = ""):
        self._host = host or config.OBS_HOST
        self._port = int(port or config.OBS_PORT)
        self._password = password if password is not None else (config.OBS_PASSWORD or "")
        self._client: Optional[Any] = None
        self._lock = threading.RLock()
        self._local_state_recording = False
        self._last_error: Optional[str] = None
        self._cached_status: Optional[ObsStatus] = None
        self._cached_status_ts: float = 0.0
        self._cached_status_ttl: float = 1.0
        self._preferred_exe: Optional[str] = None

    # ------------------------------ configuration ----------------------------
    def configure(self, host: Optional[str] = None, port: Optional[int] = None,
                  password: Optional[str] = ""):
        with self._lock:
            if host is not None:
                self._host = host
            if port is not None:
                try:
                    self._port = int(port)
                except (TypeError, ValueError):
                    pass
            if password is not None:
                self._password = password
            self._client = None
            self._cached_status = None

    def set_preferred_exe(self, path: Optional[str]):
        self._preferred_exe = path if (path and isinstance(path, str)) else None

    # ------------------------------ discovery --------------------------------
    def discover_installations(self) -> List[ObsInstallation]:
        return discover(self._preferred_exe)

    # ------------------------------ status cache -----------------------------
    def get_status(self, force: bool = False) -> ObsStatus:
        with self._lock:
            now = time.time()
            if (not force and self._cached_status
                    and (now - self._cached_status_ts) < self._cached_status_ttl):
                return self._cached_status
            status = self._build_status_locked()
            self._cached_status = status
            self._cached_status_ts = now
            return status

    def _build_status_locked(self) -> ObsStatus:
        """Construit le statut en tenant le lock (utilisé par get_status)."""
        running = is_obs_process_running()
        client = self._client
        connected = False
        scene = None
        version = None
        ws_version = None
        output_dir = None

        if client is not None:
            try:
                v = client.get_version()
                connected = True
                version = getattr(v, "obs_version", None)
                ws_version = _extract_ws_version(v)
            except Exception:
                self._client = None
                connected = False

        if connected:
            try:
                scene = client.get_current_program_scene().scene_name
            except Exception:
                scene = None
            try:
                output_dir = self._read_output_dir()
            except Exception:
                output_dir = None

        rec_state = self._local_state_recording
        if connected:
            rec_state = self._sync_remote_recording_state_locked(client)
        elif running:
            # OBS tourne mais on n'est pas connecté : l'état local est suspect.
            rec_state = False

        return ObsStatus(
            running=running,
            connected=connected,
            recording=bool(rec_state),
            scene=scene,
            version=version,
            websocket_version=ws_version,
            obs_exe_path=self._preferred_exe,
            output_dir=output_dir,
            last_error=self._last_error,
        )

    def _sync_remote_recording_state_locked(self, client: Any) -> bool:
        if client is None:
            return self._local_state_recording
        try:
            s = client.get_record_status()
            active = _is_recording_active(s)
            self._local_state_recording = active
            return active
        except Exception as e:
            self._last_error = f"Lecture état enregistrement impossible : {e}"
            return self._local_state_recording

    def _extract_ws_version(self, v: Any) -> Optional[int]:
        rv = getattr(v, "rpc_version", None)
        if rv is not None:
            try:
                return int(rv)
            except (TypeError, ValueError):
                return None
        return None

    def _read_output_dir(self) -> Optional[str]:
        client = self._client
        if client is None:
            return None
        # v5
        try:
            resp = client.get_profile_parameter(
                parameter_category="Output",
                parameter_name="FilenamesPath",
            )
            if resp:
                v = getattr(resp, "parameter_value", "")
                if v:
                    return v
        except Exception:
            pass
        try:
            resp = client.get_record_directory()
            if resp:
                v = getattr(resp, "record_directory", "")
                if v:
                    return v
        except Exception:
            pass
        # v4
        try:
            resp = client.get_config(path="AdvOut", section="Recording", key="RecFilePath")
            if resp:
                v = getattr(resp, "value", "")
                if v:
                    return v
        except Exception:
            pass
        return None

    # ------------------------------ lifecycle --------------------------------
    def connect(self, force: bool = False) -> bool:
        if not OBSWS_AVAILABLE:
            self._last_error = "Module obsws-python non installé."
            return False
        with self._lock:
            client = self._client
            if not force and client is not None:
                try:
                    client.get_version()
                    return True
                except Exception:
                    self._client = None
                    client = None
            last_err: Optional[BaseException] = None
            for attempt in range(1, 4):
                try:
                    new_client = ReqClient(
                        host=self._host,
                        port=self._port,
                        password=self._password,
                        timeout=5,
                    )
                    # Vérification réelle.
                    new_client.get_version()
                    self._client = new_client
                    self._last_error = None
                    self._cached_status = None
                    return True
                except Exception as e:
                    last_err = e
                    time.sleep(0.4 * attempt)
            self._client = None
            self._last_error = _humanize_connect_error(last_err)
            return False

    def disconnect(self):
        with self._lock:
            client = self._client
            self._client = None
            self._cached_status = None
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass

    def reset_client(self):
        """Force la déconnexion du client (utilisé après un changement de
        paramètres OBS par exemple)."""
        self.disconnect()

    # ------------------------------ recording --------------------------------
    def start_recording(self) -> bool:
        with self._lock:
            if self._local_state_recording and self._client is not None:
                # Vérifie que OBS confirme bien.
                if self._wait_for_recording_state_locked(self._client, True, timeout=2.0):
                    return True
                # Désynchronisation : on tente quand même.
            if self._client is None:
                ok = self._connect_unlocked()
                if not ok:
                    return False
            client = self._client
            if client is None:
                return False
            try:
                client.start_record()
                if self._wait_for_recording_state_locked(client, True, timeout=5.0):
                    self._local_state_recording = True
                    self._cached_status = None
                    return True
                self._last_error = "OBS n'a pas confirmé l'enregistrement."
                return False
            except Exception as e:
                self._last_error = f"StartRecord a échoué : {e}"
                self._client = None
                return False

    def stop_recording(self) -> bool:
        with self._lock:
            if not self._local_state_recording and self._client is not None:
                # Vérifie que OBS confirme.
                try:
                    s = self._client.get_record_status()
                    if not _is_recording_active(s):
                        return True
                except Exception:
                    return True
            if self._client is None:
                ok = self._connect_unlocked()
                if not ok:
                    return False
            client = self._client
            if client is None:
                return False
            try:
                client.stop_record()
                if self._wait_for_recording_state_locked(client, False, timeout=10.0):
                    self._local_state_recording = False
                    self._cached_status = None
                    return True
                self._last_error = "OBS n'a pas confirmé l'arrêt."
                return False
            except Exception as e:
                self._last_error = f"StopRecord a échoué : {e}"
                self._client = None
                return False

    def _connect_unlocked(self) -> bool:
        """Connexion OBS sans retenir _lock (appelé depuis start/stop_recording
        qui détiennent déjà le lock). Copie la config et délègue à connect()."""
        host = self._host
        port = self._port
        password = self._password or ""
        # connect() acquire le lock en interne — safe car RLock est réentrant,
        # mais ici on cherche à éviter un deadlock si connect() doit faire du I/O
        # long. On libère donc le lock principal, on connecte, puis on le reprends.
        self._lock.release()
        try:
            ok = self.connect(force=True)
        finally:
            self._lock.acquire()
        return ok

    def _wait_for_recording_state_locked(self, client: Any, expected: bool,
                                         timeout: float) -> bool:
        if client is None:
            return False
        deadline = time.time() + max(0.5, timeout)
        while time.time() < deadline:
            try:
                s = client.get_record_status()
                active = _is_recording_active(s)
                if active == expected:
                    return True
            except Exception:
                return False
            time.sleep(0.25)
        return False

    def get_recording_path(self) -> Optional[str]:
        return self.get_status(force=True).output_dir

    # ------------------------------ launch -----------------------------------
    def launch(self, exe_path: Optional[str] = None,
               timeout: Optional[float] = None,
               minimize: bool = True) -> bool:
        """Lance OBS discrètement (sans vol de focus) puis attend la
        disponibilité du WebSocket."""
        timeout = timeout or config.OBS_LAUNCH_TIMEOUT_S
        target = exe_path or self._preferred_exe

        if not target:
            installations = self.discover_installations()
            running_inst = next((i for i in installations if i.running), None)
            if running_inst:
                # Déjà lancé — on s'assure juste qu'il est joignable.
                return self._wait_websocket_ready(timeout=min(timeout, 5.0))
            inst = installations[0] if installations else None
            if not inst:
                self._last_error = "Aucune installation OBS détectée."
                return False
            target = inst.path

        if not target or not os.path.isfile(target):
            self._last_error = f"Exécutable OBS introuvable : {target}"
            return False

        if is_obs_process_running():
            logger.info("OBS est déjà en cours d'exécution.")
            return self._wait_websocket_ready(timeout=min(timeout, 8.0))

        try:
            kwargs: Dict[str, Any] = dict(cwd=os.path.dirname(target))
            if sys.platform == "win32":
                flags = 0x08000000  # CREATE_NO_WINDOW
                if minimize:
                    flags |= 0x00000007  # SW_SHOWMINNOACTIVE
                si = subprocess.STARTUPINFO()
                si.dwFlags = flags
                kwargs["startupinfo"] = si
                kwargs["creationflags"] = flags
                kwargs["close_fds"] = True
            else:
                kwargs["stdout"] = subprocess.DEVNULL
                kwargs["stderr"] = subprocess.DEVNULL
                kwargs["close_fds"] = True
            subprocess.Popen([target], **kwargs)
        except Exception as e:
            self._last_error = f"Impossible de lancer OBS : {e}"
            return False
        return self._wait_websocket_ready(timeout=timeout)

    def _wait_websocket_ready(self, timeout: float) -> bool:
        """Poll la disponibilité du WebSocket sans bloquer longtemps."""
        deadline = time.time() + max(2.0, timeout)
        with self._lock:
            self._client = None
            self._cached_status = None
        while time.time() < deadline:
            if not is_obs_process_running():
                time.sleep(0.4)
                continue
            if self.connect():
                logger.success("OBS prêt (WebSocket joignable).")
                return True
            time.sleep(config.OBS_LAUNCH_POLL_S)
        self._last_error = "OBS lancé mais WebSocket indisponible (vérifiez le mot de passe)."
        return False


# =============================================================================
# Helpers
# =============================================================================
def _is_recording_active(record_status: Any) -> bool:
    """Compatibilité obs-websocket v4 (output_active) et v5 (output_active
    ou output_state == OBS_WEBSOCKET_OUTPUT_OUTPUT_STATE_ACTIVE)."""
    if record_status is None:
        return False
    try:
        if getattr(record_status, "output_active", False):
            return True
    except Exception:
        pass
    state = getattr(record_status, "output_state", None) or getattr(record_status, "state", None)
    if not state:
        return False
    state_s = str(state)
    return ("ACTIVE" in state_s) and ("STARTING" not in state_s)


def _humanize_connect_error(exc: Optional[BaseException]) -> str:
    """Transforme une erreur brute en message lisible pour l'utilisateur."""
    if exc is None:
        return "Connexion OBS impossible."
    msg = str(exc)
    low = msg.lower()
    if "refused" in low or "10061" in low or "actively refused" in low:
        return "OBS détecté mais impossible de se connecter au WebSocket. Vérification automatique en cours…"
    if "timeout" in low or "timed out" in low:
        return "OBS met trop de temps à répondre. Réessai automatique en cours…"
    if "auth" in low or "password" in low or "401" in low or "credentials" in low:
        return "Mot de passe OBS rejeté. Vérifiez les paramètres."
    if "getaddrinfo" in low or "name resolution" in low:
        return "Adresse OBS introuvable."
    if "permission" in low or "access" in low:
        return "Accès OBS refusé. Vérifiez le mot de passe."
    return f"Connexion OBS impossible : {msg}"
