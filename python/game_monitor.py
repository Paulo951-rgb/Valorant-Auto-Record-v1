# game_monitor.py
"""
GameMonitor — boucle de surveillance avec machine à états robuste.

États :
  IDLE              : en attente / Valorant fermé
  VALORANT_LAUNCHED : Valorant ouvert mais pas en partie
  MATCH_LOADING     : passage en PREGAME / agent select
  MATCH_ACTIVE      : en partie réelle (INGAME / GAMEMODE)
  MATCH_FINISHING   : transition de fin (état transitoire avant arrêt OBS)
  RECORDING_STOPPING: arrêt OBS demandé
  FINALIZING        : renommage + finalisation fichier
  COMPLETED         : terminé, prêt pour le prochain match

Mécanismes clés :
  * Anti-rebond : 30s entre 2 enregistrements.
  * Finalisation protégée par un verrou (idempotente).
  * Reconnexion automatique d'OBS en cas d'erreur.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, Callable

import config
import logger
from obs_controller import (
    is_obs_running,
    test_obs_connection,
    start_record,
    stop_record,
    is_recording,
    get_obs_status,
    get_recording_path,
    launch_obs,
)
from valorant_api import RiotClientNotRunning
from valorant_service import (
    ValorantDataService,
    RECORDABLE_STATES,
)
import file_manager
import match_repository


class GameState(str, Enum):
    IDLE = "IDLE"
    VALORANT_LAUNCHED = "VALORANT_LAUNCHED"
    MATCH_LOADING = "MATCH_LOADING"
    MATCH_ACTIVE = "MATCH_ACTIVE"
    MATCH_FINISHING = "MATCH_FINISHING"
    RECORDING_STOPPING = "RECORDING_STOPPING"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"


class GameMonitor:
    def __init__(self, get_runtime_config: Callable[[], dict],
                 emit_event: Callable[[str, dict], None],
                 obs_service=None,
                 valo_service: Optional[ValorantDataService] = None,
                 repository: Optional[match_repository.MatchRepository] = None):
        self._get_cfg = get_runtime_config
        self._emit_event = emit_event
        self._valo = valo_service or ValorantDataService()
        self._repo = repository or match_repository.MatchRepository()

        self._running = False
        self._auto_record = True
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # State machine protégée.
        self._state_lock = threading.RLock()
        self._state = GameState.IDLE
        self._previous_state: Optional[str] = None
        self._current_session = None
        self._finalize_lock = threading.Lock()
        self._finalize_in_progress = False

        # Match en cours.
        self._match = self._new_match_template()
        self._record_start_ts: Optional[float] = None
        self._last_status_json: Optional[str] = None
        self._last_error: Optional[str] = None
        self._match_cooldown_until = 0.0
        self._last_completed_match_id: Optional[str] = None

    # ---------- properties ----------
    @property
    def running(self) -> bool:
        return self._running

    @property
    def auto_record(self) -> bool:
        return self._auto_record

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state.value

    @property
    def current_match_id(self) -> Optional[str]:
        with self._state_lock:
            return self._match.get("match_id")

    # ---------- control ----------
    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="GameMonitor")
        self._thread.start()
        logger.info("Surveillance démarrée.")
        self._emit_status(force=True)

    def stop(self, join_timeout: float = 2.0):
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        logger.info("Surveillance arrêtée.")
        # Ne bloque pas indéfiniment.
        t = self._thread
        if t and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=join_timeout)
        self._emit_status(force=True)

    def set_auto_record(self, enabled: bool):
        self._auto_record = bool(enabled)
        logger.info(f"Enregistrement automatique : {'activé' if self._auto_record else 'désactivé'}.")
        self._emit_status(force=True)

    # ---------- manual commands ----------
    def manual_start_record(self) -> bool:
        with self._state_lock:
            if is_recording():
                self._state = GameState.MATCH_ACTIVE
                return True
            if not is_obs_running() and not launch_obs():
                logger.error("Démarrage manuel impossible : OBS introuvable.")
                self._last_error = "OBS introuvable / impossible à lancer."
                return False
            if not test_obs_connection():
                logger.error("Démarrage manuel impossible : OBS injoignable.")
                self._last_error = "OBS injoignable."
                return False
            ok = start_record()
            if ok:
                self._record_start_ts = time.time()
                if not self._match.get("match_id"):
                    self._match["match_id"] = self._make_local_match_id()
                self._match["started_at"] = self._record_start_ts
                self._state = GameState.MATCH_ACTIVE
            else:
                self._last_error = "OBS a refusé de démarrer l'enregistrement."
        self._emit_status(force=True)
        return ok

    def manual_stop_record(self) -> bool:
        with self._state_lock:
            if not is_recording():
                # État déjà arrêté, on tente quand même de finaliser pour
                # traiter un éventuel match en cours.
                self._state = GameState.FINALIZING
            else:
                self._state = GameState.RECORDING_STOPPING
        ok = stop_record()
        if ok:
            self._record_start_ts = None
        self._finalize_current_match()
        self._emit_status(force=True)
        return ok

    # ---------- main loop ----------
    def _loop(self):
        while self._running and not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                self._last_error = str(e)
                logger.error("Erreur inattendue dans la boucle de surveillance", e)
            # Attend le prochain tick ou l'arrêt.
            self._stop_event.wait(self._poll_interval())

    def _poll_interval(self) -> float:
        try:
            return max(0.5, float(self._get_cfg().get("poll_interval", config.POLL_INTERVAL)))
        except Exception:
            return config.POLL_INTERVAL

    def _tick(self):
        cfg = self._get_cfg()
        self._auto_record = bool(cfg.get("auto_record", True))
        # Recharge cfg.OBS_EXE_PATH si changé.
        preferred = cfg.get("obs_exe_path")
        if preferred:
            try:
                from obs_controller import set_preferred_exe
                set_preferred_exe(preferred)
            except Exception:
                pass
        # Synchronise le mot de passe OBS à chaud.
        try:
            from obs_controller import configure
            configure(host=cfg.get("obs_host", config.OBS_HOST),
                      port=cfg.get("obs_port", config.OBS_PORT),
                      password=cfg.get("obs_password", ""))
        except Exception:
            pass

        # Lecture session (cache court).
        try:
            session = self._valo.get_current_session(use_cache=True)
        except RiotClientNotRunning:
            session = None
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"Erreur API Riot : {e}")
            session = None

        # Si pas de session ET Valorant pas running ET on n'est pas en cours
        # d'enregistrement : on note IDLE. Sinon on tente de finaliser.
        val_running, riot_running, _ = self._valo.processes_snapshot(use_cache=True)
        if session is None and not val_running and not is_recording():
            with self._state_lock:
                if self._state != GameState.IDLE:
                    self._state = GameState.IDLE
                    self._previous_state = None
            self._last_error = None
            self._current_session = None
            self._emit_status()
            return

        if session is None:
            # Pas de session lisible (lockfile illisible, etc.) mais on a
            # peut-être un enregistrement en cours.
            self._on_valorant_not_running()
            self._emit_status()
            return

        self._last_error = None
        self._current_session = session
        self._handle_session_state(session, cfg)

    def _on_valorant_not_running(self):
        with self._state_lock:
            recording_active = is_recording()
            if recording_active and self._state == GameState.MATCH_ACTIVE:
                logger.warning("Connexion Riot perdue pendant une partie : arrêt du record.")
                try:
                    stop_record()
                except Exception:
                    pass
                self._state = GameState.MATCH_FINISHING
                self._record_start_ts = None
            if self._state != GameState.IDLE:
                self._state = GameState.IDLE
                self._previous_state = None

    def _handle_session_state(self, session, cfg):
        prev = self._previous_state
        s = session.state
        is_recordable = s in RECORDABLE_STATES
        is_loading = s in ("MENUS", "PREGAME", "SPECTATING")

        with self._state_lock:
            if is_recordable and prev not in RECORDABLE_STATES:
                # Anti-double-déclenchement
                if self._state == GameState.MATCH_ACTIVE and is_recording():
                    logger.debug("Transition ignorée (déjà en MATCH_ACTIVE).")
                else:
                    self._on_match_start(session, cfg)
            elif is_loading and prev in RECORDABLE_STATES:
                if self._state == GameState.IDLE:
                    logger.debug("Transition ignorée (déjà IDLE).")
                else:
                    self._on_match_end(session, cfg)
            elif is_loading:
                if self._state not in (GameState.MATCH_LOADING, GameState.VALORANT_LAUNCHED):
                    self._state = GameState.MATCH_LOADING
        self._previous_state = s
        self._emit_status()

    # ---------- transitions ----------
    def _on_match_start(self, session, cfg):
        if not self._auto_record:
            logger.info("Début de partie détecté mais auto_record est désactivé.")
            self._state = GameState.MATCH_LOADING
            return
        # Anti-rebond
        if time.time() < self._match_cooldown_until:
            logger.info("Match ignoré (cooldown).")
            return

        self._state = GameState.MATCH_LOADING
        logger.info(f"Début de partie détecté : {session.map} ({session.agent}).")
        # OBS : si pas lancé, lancer discrètement
        if not is_obs_running():
            launched = launch_obs()
            if not launched:
                logger.error("Impossible de lancer OBS pour enregistrer.")
                self._last_error = "OBS introuvable / impossible à lancer."
                return
        if not test_obs_connection():
            logger.error("OBS détecté mais WebSocket indisponible.")
            self._last_error = "OBS détecté mais WebSocket indisponible."
            return
        if start_record():
            self._record_start_ts = time.time()
            self._state = GameState.MATCH_ACTIVE
            self._match = {
                "started_at": self._record_start_ts,
                "ended_at": None,
                "duration_s": 0,
                "match_id": self._make_local_match_id(session=session),
                "map": session.map,
                "agent": session.agent,
                "score": session.score,
                "ally_score": session.ally_score,
                "enemy_score": session.enemy_score,
                "result": "Inconnu",
                "mode": session.mode or session.queue_id,
                "queue_id": session.queue_id,
                "video_path": None,
                "file_size_bytes": 0,
            }
            self._emit_event("match_started", {
                "match_id": self._match["match_id"],
                "map": self._match["map"],
                "agent": self._match["agent"],
                "mode": self._match.get("mode"),
            })
        else:
            logger.error("OBS a refusé de démarrer l'enregistrement.")
            self._last_error = "OBS a refusé de démarrer l'enregistrement."

    def _on_match_end(self, session, cfg):
        logger.info("Fin de partie détectée.")
        self._state = GameState.MATCH_FINISHING
        if is_recording():
            self._state = GameState.RECORDING_STOPPING
            try:
                stop_record()
            except Exception as e:
                logger.error(f"Erreur stop_record : {e}")
        self._record_start_ts = None
        self._finalize_current_match(session=session)

    # ---------- finalisation ----------
    def _finalize_current_match(self, session=None, forced: bool = False):
        """Finalise le match en cours. Idempotent grâce à _finalize_lock."""
        if not self._finalize_lock.acquire(blocking=False):
            # Une finalisation est déjà en cours.
            logger.debug("Finalisation déjà en cours, ignorée.")
            return
        try:
            self._finalize_current_match_locked(session=session, forced=forced)
        finally:
            self._finalize_lock.release()

    def _finalize_current_match_locked(self, session=None, forced: bool = False):
        with self._state_lock:
            if self._state == GameState.COMPLETED:
                return
            current_match = dict(self._match)
            current_state = self._state
            self._state = GameState.FINALIZING

        cfg = self._get_cfg()
        # Met à jour le score si on a la session
        if session is not None:
            if session.score:
                current_match["score"] = session.score
            if session.ally_score:
                current_match["ally_score"] = session.ally_score
            if session.enemy_score:
                current_match["enemy_score"] = session.enemy_score
            if current_match.get("map") in (None, "Inconnu", ""):
                current_match["map"] = session.map
            if current_match.get("agent") in (None, "Inconnu", ""):
                current_match["agent"] = session.agent
            if session.mode and not current_match.get("mode"):
                current_match["mode"] = session.mode

        # Calcul du résultat
        if current_match["ally_score"] > current_match["enemy_score"]:
            result = "Victoire"
        elif current_match["ally_score"] < current_match["enemy_score"]:
            result = "Defaite"
        else:
            result = "Inconnu"
        current_match["result"] = result

        # Détermine le dossier de sortie (OBS si possible, sinon config)
        try:
            obs_folder = get_recording_path() or cfg.get("obs_folder", os.path.expanduser("~/Videos"))
        except Exception:
            obs_folder = cfg.get("obs_folder", os.path.expanduser("~/Videos"))

        if not obs_folder or not isinstance(obs_folder, str):
            obs_folder = os.path.expanduser("~/Videos")
        obs_folder = os.path.expanduser(obs_folder)

        # Attente du fichier finalisé
        started = current_match.get("started_at") or 0
        try:
            finalized = file_manager.wait_for_finalized_recording(
                obs_folder,
                since_ts=started,
                timeout=15.0,
                stable_seconds=2.0,
            )
        except Exception as e:
            logger.error(f"Erreur attente finalisation: {e}")
            finalized = None

        video_path = None
        file_size = 0
        if finalized:
            template = cfg.get("file_naming", "Valorant_{date}_{map}_{agent}_{score}_{result}")
            try:
                video_path, file_size = file_manager.rename_recording_with_options(
                    obs_folder, current_match["map"], current_match["agent"],
                    current_match["score"], result, template=template, file_path=finalized,
                )
            except Exception as e:
                logger.error(f"Erreur renommage: {e}")
            if video_path and os.path.exists(video_path):
                try: file_size = file_size or os.path.getsize(video_path)
                except OSError: pass

        current_match["video_path"] = video_path
        current_match["file_size_bytes"] = file_size
        if started:
            current_match["duration_s"] = int(time.time() - started)
        date_iso = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Insertion en base
        if current_match["match_id"]:
            try:
                self._repo.upsert_match({
                    "match_id": current_match["match_id"],
                    "date": date_iso,
                    "map_name": current_match["map"],
                    "agent": current_match["agent"],
                    "score": current_match["score"],
                    "ally_score": current_match["ally_score"],
                    "enemy_score": current_match["enemy_score"],
                    "result": result,
                    "mode": current_match.get("mode"),
                    "queue_id": current_match.get("queue_id"),
                    "duration_seconds": current_match["duration_s"],
                    "video_path": video_path,
                    "file_size_bytes": file_size,
                    "status": "completed" if video_path else "failed",
                })
                self._last_completed_match_id = current_match["match_id"]
            except Exception as e:
                logger.error(f"Erreur écriture BDD : {e}")

        # Nettoyage (en protégeant le fichier en cours de finalisation)
        try:
            protected = [video_path] if video_path else None
            file_manager.clean_old_recordings(obs_folder, float(cfg.get("max_size_gb", 50)),
                                              protected_paths=protected)
        except Exception as e:
            logger.debug(f"Nettoyage ancien: {e}")

        # Cooldown anti-rebond
        self._match_cooldown_until = time.time() + 30

        # Émission événement
        self._emit_event("match_ended", {
            "match_id": current_match["match_id"],
            "map": current_match["map"],
            "agent": current_match["agent"],
            "score": current_match["score"],
            "result": result,
            "path": video_path,
            "duration_seconds": current_match["duration_s"],
        })

        # Reset état
        with self._state_lock:
            self._state = GameState.COMPLETED
            self._match = self._new_match_template()

    def _new_match_template(self) -> Dict[str, Any]:
        return {
            "started_at": None, "ended_at": None, "duration_s": 0,
            "match_id": None, "map": "Inconnu", "agent": "Inconnu",
            "score": "0-0", "ally_score": 0, "enemy_score": 0,
            "result": "Inconnu", "mode": None, "queue_id": None,
            "video_path": None, "file_size_bytes": 0,
        }

    def _make_local_match_id(self, session=None) -> str:
        if session is not None and session.queue_id and session.map not in (None, "Inconnu", ""):
            return f"{config.LOCAL_MATCH_PREFIX}{int(time.time())}_{session.queue_id}"
        return f"{config.LOCAL_MATCH_PREFIX}{uuid.uuid4().hex[:12]}"

    # ---------- status ----------
    def record_duration(self) -> int:
        if is_recording() and self._record_start_ts:
            return int(time.time() - self._record_start_ts)
        return 0

    def snapshot(self) -> Dict[str, Any]:
        cfg = self._get_cfg()
        try:
            obs = get_obs_status()
        except Exception as e:
            logger.debug(f"obs status error: {e}")
            obs = {"running": False, "connected": False, "recording": False,
                   "scene": None, "version": None, "websocket_version": None,
                   "obs_exe_path": None, "output_dir": None}
        s = self._current_session
        with self._state_lock:
            return {
                "monitoring": self._running,
                "auto_record": self._auto_record,
                "state": self._state.value,
                "obs": obs,
                "valorant_running": self._valo.is_valorant_running(),
                "riot_connected": self._valo.riot_lockfile_present() and self._valo.is_riot_running(),
                "session_state": s.state if s else "Indisponible",
                "session_label": s.state_label if s else "Indisponible",
                "map": s.map if s else "Inconnu",
                "agent": s.agent if s else "Inconnu",
                "score": s.score if s else "0-0",
                "ally_score": s.ally_score if s else 0,
                "enemy_score": s.enemy_score if s else 0,
                "mode": s.mode if s else None,
                "queue_id": s.queue_id if s else None,
                "recording": is_recording(),
                "recording_duration": self.record_duration(),
                "output_dir": obs.get("output_dir") or cfg.get("obs_folder", ""),
                "last_error": self._last_error,
                "current_match_id": self._match.get("match_id"),
            }

    def _emit_status(self, force: bool = False):
        try:
            data = self.snapshot()
        except Exception as e:
            self._last_error = str(e)
            return
        import json as _json
        try:
            js = _json.dumps(data, sort_keys=True, default=str)
        except Exception:
            return
        if not force and js == self._last_status_json:
            return
        self._last_status_json = js
        try:
            self._emit_event("status", data)
        except Exception as e:
            logger.error("Erreur emission événement status", e)


# Compatibilité ascendante
def is_valorant_running() -> bool:
    return ValorantDataService.is_valorant_running()


def riot_connected_value() -> bool:
    v = ValorantDataService()
    return v.riot_lockfile_present() and v.is_riot_running()
