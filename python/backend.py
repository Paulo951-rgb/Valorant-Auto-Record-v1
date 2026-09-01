#!/usr/bin/env python3
# backend.py
"""
Backend Python pour l'application Electron "Valorant Auto Record".

Communication : protocole JSON-lines sur stdin/stdout.
  * Requête  (entrée)  : {"id": "...", "method": "...", "params": {...}}
  * Réponse (sortie)   : {"id": "...", "result": ..., "error": null}
  * Notification        : {"event": "...", "data": {...}}

Ce module CONSERVE intégralement la logique métier d'origine (OBS,
Valorant, SQLite, fichiers). Il orchestre les services et expose une
IPC riche à l'interface Electron.
"""
import os
import sys
import json
import re
import time
import threading
import traceback
import uuid
from datetime import datetime
from typing import Any, Dict

# --- Redirection de stdout AVANT tout print -------------------------------
_ORIGINAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)

# --- Imports métier --------------------------------------------------------
import config
import logger
import database
import file_manager
import match_repository
from obs_controller import (
    is_obs_running,
    launch_obs,
    test_obs_connection,
    start_record,
    stop_record,
    is_recording,
    get_obs_status,
    get_recording_path,
    configure as configure_obs,
    discover_installations as discover_obs,
)
from valorant_api import get_current_state, RiotClientNotRunning
from game_monitor import GameMonitor

# --- Fichiers ---------------------------------------------------------------
CONFIG_FILE = os.path.join(_HERE, "config_local.json")
LOG_DIR = os.path.abspath(os.path.join(_HERE, "..", "logs"))
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "app.log")

DEFAULT_CONFIG = {
    "obs_host": config.OBS_HOST,
    "obs_port": config.OBS_PORT,
    "obs_password": config.OBS_PASSWORD,
    "obs_folder": os.path.expanduser("~/Videos"),
    "obs_exe_path": r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
    "poll_interval": config.POLL_INTERVAL,
    "auto_record": True,
    "log_level": "INFO",
    "max_size_gb": 50,
    "max_duration_minutes": 60,
    "record_format": "mp4",
    "file_naming": "Valorant_{date}_{map}_{agent}_{score}_{result}",
    "start_with_windows": False,
    "minimize_to_tray": True,
    "show_advanced": False,
    "auto_launch_obs": True,
    "history_keep": True,
}

LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "SUCCESS": 20, "WARNING": 30, "ERROR": 40}

APP_VERSION = "2.0.0"


# =========================================================================
# CONFIGURATION
# =========================================================================
class ConfigStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._cfg = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        with self._lock:
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                    if isinstance(saved, dict):
                        for k, v in saved.items():
                            if k in self._cfg:
                                self._cfg[k] = v
                except Exception as e:
                    _log_to_file(f"Erreur lecture config : {e}")
            self._apply_to_module()

    def save(self, new_values):
        with self._lock:
            if isinstance(new_values, dict):
                for k, v in new_values.items():
                    if k in self._cfg:
                        self._cfg[k] = v
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(self._cfg, f, indent=2)
            except Exception as e:
                _log_to_file(f"Erreur écriture config : {e}")
            self._apply_to_module()
        return dict(self._cfg)

    def get(self):
        with self._lock:
            return dict(self._cfg)

    def reset(self):
        with self._lock:
            self._cfg = dict(DEFAULT_CONFIG)
            self._apply_to_module()
        return dict(self._cfg)

    def _apply_to_module(self):
        config.OBS_HOST = self._cfg.get("obs_host", getattr(config, "OBS_HOST", "localhost"))
        try:
            config.OBS_PORT = int(self._cfg.get("obs_port", getattr(config, "OBS_PORT", 4455)))
        except Exception:
            pass
        config.OBS_PASSWORD = self._cfg.get("obs_password", getattr(config, "OBS_PASSWORD", ""))
        config.OBS_EXE_PATH = self._cfg.get(
            "obs_exe_path",
            getattr(config, "OBS_EXE_PATH", r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"),
        )
        try:
            config.POLL_INTERVAL = float(
                self._cfg.get("poll_interval", getattr(config, "POLL_INTERVAL", 2))
            )
        except Exception:
            pass
        # Synchronise OBS
        try:
            configure_obs(host=config.OBS_HOST, port=config.OBS_PORT,
                           password=config.OBS_PASSWORD)
        except Exception:
            pass
        # Synchronise le chemin EXÉ preferé sur le service partagé.
        try:
            from obs_controller import set_preferred_exe
            set_preferred_exe(config.OBS_EXE_PATH)
        except Exception:
            pass


store = ConfigStore()


# =========================================================================
# JOURNALISATION
# =========================================================================
_file_log_lock = threading.Lock()
_log_file_handle = None


def _close_log_file():
    global _log_file_handle
    if _log_file_handle is not None:
        try:
            _log_file_handle.flush()
            _log_file_handle.close()
        except Exception:
            pass
        _log_file_handle = None


import atexit
atexit.register(_close_log_file)


def _open_log_file():
    global _log_file_handle
    try:
        _log_file_handle = open(LOG_FILE, "a", encoding="utf-8")
    except Exception:
        _log_file_handle = None


def _log_to_file(message):
    global _log_file_handle
    if _log_file_handle is None:
        _open_log_file()
    if _log_file_handle is None:
        return
    try:
        with _file_log_lock:
            _log_file_handle.write(message + "\n")
            _log_file_handle.flush()
    except Exception:
        pass


def _level_allowed(level):
    configured = store.get().get("log_level", "INFO").upper()
    return LOG_LEVELS.get(level.upper(), 20) >= LOG_LEVELS.get(configured, 20)


def _on_log(formatted_msg):
    _log_to_file(formatted_msg)
    try:
        m = re.match(r"^\[(?P<time>[^\]]+)\]\s*\[(?P<level>[^\]]+)\]\s*(?P<msg>.*)$",
                     formatted_msg, re.DOTALL)
        if m:
            level = m.group("level").strip()
            message = m.group("msg")
        else:
            level, message = "INFO", formatted_msg
    except Exception:
        level, message = "INFO", formatted_msg
    if not _level_allowed(level):
        return
    _send_notification("log", {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "message": message,
        "raw": formatted_msg,
    })


logger.ui_callback = _on_log


# =========================================================================
# PROTOCOLE JSON-LINES
# =========================================================================
_send_lock = threading.Lock()


def _send(obj):
    try:
        line = json.dumps(obj, ensure_ascii=False, default=str)
        with _send_lock:
            _ORIGINAL_STDOUT.write(line + "\n")
            _ORIGINAL_STDOUT.flush()
    except Exception as e:
        _log_to_file(f"Erreur emission JSON : {e}\n{traceback.format_exc()}")


def _send_response(req_id, result, error=None):
    _send({"id": req_id, "result": result, "error": error})


def _send_notification(event, data):
    _send({"event": event, "data": data})


def _emit_event(name, data):
    _send_notification(name, data)


# =========================================================================
# MONITOR
# =========================================================================
repository = match_repository.MatchRepository()
repository.init()
monitor = GameMonitor(get_runtime_config=store.get,
                      emit_event=_emit_event,
                      repository=repository)


# =========================================================================
# COMMANDES
# =========================================================================
def cmd_ping(params):
    return {"pong": True, "time": datetime.now().isoformat()}


def cmd_get_version(params):
    return {"version": APP_VERSION}


def cmd_get_status(params):
    return monitor.snapshot()


def cmd_get_config(params):
    return store.get()


def cmd_save_config(params):
    saved = store.save(params or {})
    logger.success("Configuration enregistrée.")
    _send_notification("config_changed", saved)
    return saved


def cmd_reset_config(params):
    saved = store.reset()
    logger.success("Configuration réinitialisée.")
    _send_notification("config_changed", saved)
    return saved


def cmd_start_monitoring(params):
    monitor.start()
    return {"monitoring": monitor.running}


def cmd_stop_monitoring(params):
    monitor.stop()
    return {"monitoring": monitor.running}


def cmd_set_auto_record(params):
    enabled = params.get("enabled", True) if isinstance(params, dict) else True
    monitor.set_auto_record(bool(enabled))
    return {"auto_record": monitor.auto_record}


def cmd_start_record(params):
    ok = monitor.manual_start_record()
    return {"started": ok, "recording": is_recording()}


def cmd_stop_record(params):
    ok = monitor.manual_stop_record()
    return {"stopped": ok, "recording": is_recording()}


def cmd_test_obs(params):
    ok = test_obs_connection()
    return {"connected": ok, "running": is_obs_running()}


def cmd_reconnect_obs(params):
    # Force une reconnexion propre (réinitialise le client mis en cache).
    from obs_controller import _service as _shared_service
    _shared_service.reset_client()
    ok = test_obs_connection()
    return {"connected": ok, "running": is_obs_running()}


def cmd_launch_obs(params):
    cfg = store.get()
    preferred = cfg.get("obs_exe_path")
    if preferred:
        from obs_controller import set_preferred_exe
        set_preferred_exe(preferred)
    ok = launch_obs()
    return {"launched": ok, "running": is_obs_running(),
            "recording_path": get_recording_path()}


def cmd_discover_obs(params):
    installations = discover_obs()
    return {
        "installations": [
            {"path": i.path, "version": i.version, "running": i.running}
            for i in installations
        ],
        "count": len(installations),
    }


def cmd_get_recording_path(params):
    return {"output_dir": get_recording_path()}


def cmd_get_history(params):
    return repository.get_all()


def cmd_delete_match(params):
    match_id = (params or {}).get("match_id")
    if not match_id:
        return {"deleted": False, "error": "match_id manquant"}
    return {"deleted": repository.delete(match_id)}


def cmd_update_match(params):
    match_id = (params or {}).get("match_id")
    if not match_id:
        return {"updated": False, "error": "match_id manquant"}
    data = {k: v for k, v in (params or {}).items() if k != "match_id"}
    repository.upsert_match({"match_id": match_id, **data})
    return {"updated": True}


def cmd_get_valo_state(params):
    try:
        state = get_current_state(debug=config.DEBUG)
        return {"ok": True, "data": state}
    except RiotClientNotRunning:
        return {"ok": False, "error": "Riot Client non détecté (Valorant éteint)."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_app_diagnostics(params):
    """Page de diagnostic destinée aux utilisateurs avancés."""
    cfg = store.get()
    try:
        installations = discover_obs()
    except Exception as e:
        installations = []
        logger.debug(f"discover_obs error: {e}")
    try:
        rec_path = get_recording_path()
    except Exception:
        rec_path = None
    try:
        obs_status = get_obs_status()
    except Exception as e:
        obs_status = {"running": False, "connected": False, "recording": False,
                      "scene": None, "version": None, "websocket_version": None,
                      "obs_exe_path": None, "output_dir": None}
        logger.debug(f"obs_status error: {e}")
    db_path = os.path.abspath(repository._db_path)
    log_path = LOG_FILE
    out = {
        "config": cfg,
        "obs_installations": [
            {"path": i.path, "version": i.version, "running": i.running}
            for i in installations
        ],
        "obs_status": obs_status,
        "recording_path": rec_path,
        "db_path": db_path,
        "log_path": log_path,
    }
    if cfg.get("log_level") == "DEBUG":
        try:
            out["session_state"] = get_current_state(debug=True)
        except Exception as e:
            out["session_state"] = {"error": str(e)}
    return out


def cmd_quit(params):
    """Demande au processus Python de se terminer proprement (utilisé
    par Electron pour before-quit)."""
    import threading as _t
    def _shutdown():
        time.sleep(0.2)
        if monitor.running:
            monitor.stop()
        time.sleep(0.3)
        # Ferme proprement le fichier de log (os._exit contourne atexit).
        _close_log_file()
        os._exit(0)
    _t.Timer(0.05, _shutdown).start()
    return {"quit": True}


HANDLERS = {
    "ping": cmd_ping,
    "get_version": cmd_get_version,
    "get_status": cmd_get_status,
    "get_config": cmd_get_config,
    "save_config": cmd_save_config,
    "reset_config": cmd_reset_config,
    "start_monitoring": cmd_start_monitoring,
    "stop_monitoring": cmd_stop_monitoring,
    "set_auto_record": cmd_set_auto_record,
    "start_record": cmd_start_record,
    "stop_record": cmd_stop_record,
    "test_obs": cmd_test_obs,
    "reconnect_obs": cmd_reconnect_obs,
    "launch_obs": cmd_launch_obs,
    "discover_obs": cmd_discover_obs,
    "get_recording_path": cmd_get_recording_path,
    "get_history": cmd_get_history,
    "delete_match": cmd_delete_match,
    "update_match": cmd_update_match,
    "get_valo_state": cmd_get_valo_state,
    "app_diagnostics": cmd_app_diagnostics,
    "quit": cmd_quit,
}


# =========================================================================
# BOUCLE STDIN
# =========================================================================
def _handle_request(line):
    try:
        req = json.loads(line)
    except Exception as e:
        _send_notification("error", {"message": f"JSON invalide : {e}"})
        return
    if not isinstance(req, dict):
        _send_notification("error", {"message": "Requête invalide : un objet JSON était attendu."})
        return
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        params = {}
    handler = HANDLERS.get(method)
    if handler is None:
        _send_response(req_id, None, error=f"Méthode inconnue : {method}")
        return
    # Exécute le handler dans un thread pour ne jamais bloquer stdin.
    import threading as _threading
    result_box: Dict[str, Any] = {}

    def _runner():
        try:
            result_box["result"] = handler(params)
        except Exception as e:
            result_box["error"] = e
            result_box["tb"] = traceback.format_exc()

    t = _threading.Thread(target=_runner, daemon=True)
    t.start()
    # Timeout dur : 5 min pour les handlers longs (launch_obs, finalize).
    timeout_s = 300
    t.join(timeout=timeout_s)
    if t.is_alive():
        _send_response(req_id, None, error=f"Timeout ({timeout_s}s) sur '{method}'.")
        return
    if "error" in result_box:
        e = result_box["error"]
        _log_to_file(f"Erreur handler {method} : {e}\n{result_box.get('tb','')}")
        logger.error(f"Erreur lors de l'exécution de '{method}'", e)
        _send_response(req_id, None, error=str(e))
    else:
        _send_response(req_id, result_box.get("result"))


def _stdin_loop():
    logger.info("Backend Python démarré.")
    _send_notification("ready", {"version": APP_VERSION, "time": datetime.now().isoformat()})
    try:
        _send_notification("status", monitor.snapshot())
    except Exception as e:
        _log_to_file(f"Erreur statut initial : {e}")
    while True:
        try:
            line = sys.stdin.readline()
        except Exception as e:
            _log_to_file(f"Erreur lecture stdin : {e}")
            time.sleep(0.5)
            continue
        if not line:
            _log_to_file("stdin fermé, arrêt du backend.")
            break
        line = line.strip()
        if not line:
            continue
        _handle_request(line)


def main():
    database.init_db()
    _open_log_file()
    logger.info("=" * 50)
    logger.info("Valorant Auto Record - Backend")
    logger.info("=" * 50)
    try:
        _stdin_loop()
    except KeyboardInterrupt:
        pass
    finally:
        if monitor.running:
            monitor.stop()
        # Ferme proprement le fichier de log.
        global _log_file_handle
        if _log_file_handle is not None:
            try:
                _log_file_handle.flush()
                _log_file_handle.close()
            except Exception:
                pass
            _log_file_handle = None


if __name__ == "__main__":
    main()
