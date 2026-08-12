#!/usr/bin/env python3
# backend.py
"""
Backend Python pour l'application Electron "Valorant Auto Record".

Communication : protocole JSON-lines sur stdin/stdout.
 - Une requête par ligne (entrée stdin)  : {"id": "...", "method": "...", "params": {...}}
 - Une réponse   par ligne (sortie stdout): {"id": "...", "result": ..., "error": null}
 - Des notifications asynchrones           : {"event": "...", "data": {...}}

Ce module CONSERVE intégralement la logique existante du projet
(config, obs_controller, valorant_api, database, file_manager, logger).
Il se contente de l'orchestrer et de l'exposer à l'interface Electron.
"""
import os
import sys
import json
import time
import threading
import traceback
from datetime import datetime

# --- Redirection de stdout AVANT tout print -------------------------------
# Le protocole JSON utilise stdout exclusivement. Toute sortie "normale"
# (print, tracebacks, avertissements) est redirigée vers stderr pour ne pas
# corrompre le protocole. Les réponses JSON s'écrivent sur le flux d'origine.
_ORIGINAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)  # DB + config_local.json résolus relativement au script.

# --- Imports de la logique existante (inchangée) --------------------------
import config
import database
import logger
from obs_controller import (
    is_obs_running,
    launch_obs,
    test_obs_connection,
    start_record,
    stop_record,
    is_recording,
    get_obs_status,
)
from valorant_api import get_current_state, RiotClientNotRunning
from monitor import Monitor, is_valorant_running, riot_connected_value

# --- Fichiers de configuration / logs ------------------------------------
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
}

LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "SUCCESS": 20, "WARNING": 30, "ERROR": 40}


# =========================================================================
# CONFIGURATION
# =========================================================================
class ConfigStore:
    """Gère config_local.json et applique les valeurs au module `config`."""

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
                        self._cfg.update(saved)
                except Exception as e:
                    _log_to_file(f"Erreur lecture config : {e}")
            self._apply_to_module()

    def save(self, new_values):
        with self._lock:
            if isinstance(new_values, dict):
                for k, v in new_values.items():
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

    def _apply_to_module(self):
        # Les modules existants lisent ces attributs au moment de l'appel.
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


store = ConfigStore()


# =========================================================================
# JOURNALISATION (fichier + redirection vers Electron)
# =========================================================================
_file_log_lock = threading.Lock()
_log_file_handle = None


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
    """ui_callback branché sur logger.py : transmet les logs à Electron."""
    # formatted_msg = "[HH:MM:SS] [LEVEL] message"
    _log_to_file(formatted_msg)
    try:
        _, rest = formatted_msg.split("] ", 1)
        level = rest.split("] ", 1)[0].strip("[")
        message = rest.split("] ", 1)[1] if "] " in rest else rest
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


# Branchement du callback de logging existant (même mécanisme que main.py).
logger.ui_callback = _on_log


# =========================================================================
# PROTOCOLE JSON-LINES
# =========================================================================
_send_lock = threading.Lock()


def _send(obj):
    """Écrit un objet JSON sur le flux stdout d'origine (atomique par ligne)."""
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
    """Callback utilisé par le Monitor pour notifier Electron."""
    _send_notification(name, data)


# =========================================================================
# MONITOR
# =========================================================================
monitor = Monitor(get_runtime_config=store.get, emit_event=_emit_event)


# =========================================================================
# DISPATCH DES COMMANDES
# =========================================================================
def cmd_get_status(params):
    return monitor.snapshot()


def cmd_get_config(params):
    return store.get()


def cmd_save_config(params):
    saved = store.save(params or {})
    logger.success("Configuration enregistrée.")
    _send_notification("config_changed", saved)
    return saved


def cmd_start_monitoring(params):
    monitor.start()
    return {"monitoring": monitor.running}


def cmd_stop_monitoring(params):
    monitor.stop()
    return {"monitoring": monitor.running}


def cmd_set_auto_record(params):
    monitor.set_auto_record(bool(params.get("enabled", True)) if params else True)
    return {"auto_record": monitor._auto_record}


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
    # Force une reconnexion (réinitialise le client mis en cache).
    import obs_controller as oc
    oc._client = None
    oc.recording = False
    ok = test_obs_connection()
    return {"connected": ok, "running": is_obs_running()}


def cmd_launch_obs(params):
    ok = launch_obs()
    return {"launched": ok, "running": is_obs_running()}


def cmd_get_history(params):
    database.init_db()
    rows = database.get_all_matches()
    return [
        {
            "id": r[0], "date": r[1], "map": r[2], "agent": r[3],
            "score": r[4], "result": r[5], "path": r[6],
        }
        for r in rows
    ]


def cmd_get_valo_state(params):
    """Récupère l'état Riot à la demande (debug / tableau de bord)."""
    try:
        return {"ok": True, "data": get_current_state(debug=config.DEBUG)}
    except RiotClientNotRunning:
        return {"ok": False, "error": "Riot Client non détecté (Valorant éteint)."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_ping(params):
    return {"pong": True, "time": datetime.now().isoformat()}


HANDLERS = {
    "ping": cmd_ping,
    "get_status": cmd_get_status,
    "get_config": cmd_get_config,
    "save_config": cmd_save_config,
    "start_monitoring": cmd_start_monitoring,
    "stop_monitoring": cmd_stop_monitoring,
    "set_auto_record": cmd_set_auto_record,
    "start_record": cmd_start_record,
    "stop_record": cmd_stop_record,
    "test_obs": cmd_test_obs,
    "reconnect_obs": cmd_reconnect_obs,
    "launch_obs": cmd_launch_obs,
    "get_history": cmd_get_history,
    "get_valo_state": cmd_get_valo_state,
}


# =========================================================================
# BOUCLE DE LECTURE STDIN
# =========================================================================
def _handle_request(line):
    try:
        req = json.loads(line)
    except Exception as e:
        _send_notification("error", {"message": f"JSON invalide : {e}"})
        return
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    handler = HANDLERS.get(method)
    if handler is None:
        _send_response(req_id, None, error=f"Méthode inconnue : {method}")
        return
    try:
        result = handler(params)
        _send_response(req_id, result)
    except Exception as e:
        _log_to_file(f"Erreur handler {method} : {e}\n{traceback.format_exc()}")
        logger.error(f"Erreur lors de l'exécution de '{method}'", e)
        _send_response(req_id, None, error=str(e))


def _stdin_loop():
    logger.info("Backend Python démarré.")
    _send_notification("ready", {"version": "1.0.0", "time": datetime.now().isoformat()})
    # Émet un premier statut pour que l'interface ne reste pas vide.
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
            # stdin fermé (Electron a quitté) -> arrêt propre.
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


if __name__ == "__main__":
    main()
