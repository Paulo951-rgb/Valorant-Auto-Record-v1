# monitor.py
"""
Boucle de surveillance extraite de main.py (logique originale conservée).

Cette classe reproduit EXACTEMENT le comportement de
`ModernRecorderApp.monitoring_loop()` du projet d'origine, à la différence
près qu'elle émet des événements (via un callback) au lieu de manipuler une
interface graphique customtkinter.

La logique métier (détection Riot -> état -> démarrage/arrêt OBS ->
renommage -> base de données -> nettoyage) est inchangée.
"""
import os
import threading
import time
import json
from datetime import datetime

import config
import database
import file_manager
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

# Noms de processus à surveiller (insensible à la casse).
VALORANT_PROCESS_NAMES = ("valorant.exe", "valorant-win64-shipping.exe")


def is_valorant_running():
    """Détecte le processus VALORANT (Windows). Retourne False sur les autres OS."""
    try:
        import psutil
    except ImportError:
        return False
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in VALORANT_PROCESS_NAMES:
                return True
    except Exception:
        return False
    return False


class Monitor:
    def __init__(self, get_runtime_config, emit_event):
        """
        get_runtime_config : callable() -> dict  (config live, modifiable à chaud)
        emit_event         : callable(event_name:str, data:dict) -> None
        """
        self._get_cfg = get_runtime_config
        self._emit = emit_event

        self._running = False
        self._auto_record = True
        self._thread = None

        # État interne de la boucle (identique à main.py)
        self._previous_state = None
        self._current_map = "Inconnu"
        self._current_agent = "Inconnu"
        self._current_score = "0-0"

        self._record_start_ts = None
        self._last_status_json = None
        self._last_error = None

    # ------------------------------------------------------------------ contrôle
    @property
    def running(self):
        return self._running

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Surveillance démarrée.")
        self._emit_status(force=True)

    def stop(self):
        if not self._running:
            return
        self._running = False
        logger.info("Surveillance arrêtée.")
        # On laisse le thread se terminer naturellement ; pas de join bloquant.
        self._emit_status(force=True)

    def set_auto_record(self, enabled):
        self._auto_record = bool(enabled)
        logger.info(f"Enregistrement automatique : {'activé' if self._auto_record else 'désactivé'}.")
        self._emit_status(force=True)

    # ------------------------------------------------------------------ commandes manuelles
    def manual_start_record(self):
        if is_recording():
            logger.warning("Démarrage manuel ignoré : enregistrement déjà actif.")
            return False
        if not is_obs_running():
            logger.warning("OBS n'est pas lancé : démarrage manuel impossible.")
            return False
        if test_obs_connection():
            ok = start_record()
            if ok:
                self._record_start_ts = time.time()
            self._emit_status(force=True)
            return ok
        logger.error("Démarrage manuel impossible : OBS injoignable.")
        self._emit_status(force=True)
        return False

    def manual_stop_record(self):
        if not is_recording():
            logger.warning("Arrêt manuel ignoré : aucun enregistrement actif.")
            return False
        ok = stop_record()
        if ok:
            self._record_start_ts = None
        self._emit_status(force=True)
        return ok

    # ------------------------------------------------------------------ boucle principale
    def _loop(self):
        """Reproduction de monitoring_loop() de main.py."""
        while self._running:
            try:
                self._tick()
            except Exception as e:
                self._last_error = str(e)
                logger.error("Erreur inattendue dans la boucle de surveillance", e)
            time.sleep(self._poll_interval())

    def _poll_interval(self):
        try:
            return float(self._get_cfg().get("poll_interval", config.POLL_INTERVAL))
        except Exception:
            return config.POLL_INTERVAL

    def _tick(self):
        cfg = self._get_cfg()
        self._auto_record = bool(cfg.get("auto_record", True))

        # 1) Récupération de l'état Riot/Valorant (logique d'origine).
        try:
            state_data = get_current_state(debug=config.DEBUG)
            state = state_data["state"]
            self._current_map = state_data["map"]
            self._current_agent = state_data["agent"]
            self._current_score = state_data.get("score", "0-0")
            riot_connected = True
        except RiotClientNotRunning:
            self._previous_state = None
            self._current_map = "Inconnu"
            self._current_agent = "Inconnu"
            self._current_score = "0-0"
            riot_connected = False
            self._emit_status()
            time.sleep(self._poll_interval())
            return
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"Erreur API Riot : {e}")
            riot_connected = False
            self._emit_status()
            time.sleep(self._poll_interval())
            return

        self._last_error = None

        # 2) Détection de début de partie -> lancement OBS + enregistrement.
        if state == "INGAME" and self._previous_state != "INGAME":
            logger.info("Détection du début de partie.")
            if self._auto_record:
                if not is_obs_running():
                    launch_obs()
                if test_obs_connection():
                    if start_record():
                        self._record_start_ts = time.time()
                        self._emit("match_started", {
                            "map": self._current_map,
                            "agent": self._current_agent,
                        })
                else:
                    logger.error("OBS n'a pas pu être rejoint pour lancer l'enregistrement.")

        # 3) Détection de fin de partie -> arrêt + renommage + base + nettoyage.
        elif self._previous_state == "INGAME" and state != "INGAME":
            logger.info("Détection de la fin du match.")
            if is_recording():
                stop_record()
                self._record_start_ts = None

            obs_folder = cfg.get("obs_folder", os.path.expanduser("~/Videos"))
            score_final = self._current_score
            try:
                scores = [int(x) for x in score_final.split("-")]
                result_str = "Victoire" if scores[0] > scores[1] else "Defaite"
            except Exception:
                result_str = "FinMatch"

            new_path = file_manager.rename_recording(
                obs_folder, self._current_map, self._current_agent, score_final, result_str
            )
            if new_path:
                date_now = datetime.now().strftime("%Y-%m-%d %H:%M")
                database.add_match(date_now, self._current_map, self._current_agent,
                                   score_final, result_str, new_path)
                max_size_gb = float(cfg.get("max_size_gb", 50))
                file_manager.clean_old_recordings(obs_folder, max_size_gb=max_size_gb)
                self._emit("match_ended", {
                    "map": self._current_map,
                    "agent": self._current_agent,
                    "score": score_final,
                    "result": result_str,
                    "path": new_path,
                })

        self._previous_state = state
        self._emit_status()

    # ------------------------------------------------------------------ statut
    def record_duration(self):
        if is_recording() and self._record_start_ts is not None:
            return int(time.time() - self._record_start_ts)
        return 0

    def snapshot(self):
        """Instantané complet de l'état (utilisé par le backend)."""
        cfg = self._get_cfg()
        obs = get_obs_status()
        return {
            "monitoring": self._running,
            "auto_record": self._auto_record,
            "obs": obs,
            "valorant_running": is_valorant_running(),
            "riot_connected": riot_connected_value(),
            "session_state": self._previous_state if self._previous_state is not None else "Indisponible",
            "map": self._current_map,
            "agent": self._current_agent,
            "score": self._current_score,
            "recording": is_recording(),
            "recording_duration": self.record_duration(),
            "output_dir": cfg.get("obs_folder", ""),
            "last_error": self._last_error,
        }

    def _build_status(self):
        return self.snapshot()

    def _emit_status(self, force=False):
        status = self._build_status()
        js = json.dumps(status, sort_keys=True)
        if not force and js == self._last_status_json:
            return
        self._last_status_json = js
        self._emit("status", status)

    def _emit(self, name, data):
        try:
            self._emit(name, data)
        except Exception as e:
            logger.error("Erreur emission événement", e)


def riot_connected_value():
    """Indique si le Riot Client est joignable (lockfile présent). Non bloquant."""
    try:
        from valorant_api import read_lockfile
        read_lockfile()
        return True
    except Exception:
        return False
