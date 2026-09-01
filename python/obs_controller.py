# obs_controller.py — shim rétro-compatible
# Toute la logique a été déplacée dans obs_service.OBSService.
# Ce module expose les symboles historiques consommés par l'ancien code.

import time
import config
import logger

from obs_service import (
    OBSService,
    ObsStatus,
    ObsInstallation,
    discover as _discover,
    is_obs_process_running,
)

_service: OBSService = OBSService()
_recording_local = {"value": False}


def _client():
    return _service._client  # noqa: SLF001


# Réexport des fonctions publiques historiques ---------------------
def is_obs_running() -> bool:
    return is_obs_process_running()


def launch_obs() -> bool:
    """Démarre OBS et attend la disponibilité du WebSocket (timeout 45s)."""
    return _service.launch(timeout=config.OBS_LAUNCH_TIMEOUT_S)


def connect_obs():
    """Renvoie le client OBS (ou None). Connexion paresseuse."""
    if _service.connect():
        return _service._client  # noqa: SLF001
    return None


def obs_available() -> bool:
    return _service.connect()


def test_obs_connection() -> bool:
    return _service.connect()


def start_record() -> bool:
    ok = _service.start_recording()
    if ok:
        _recording_local["value"] = True
    return ok


def stop_record() -> bool:
    ok = _service.stop_recording()
    if ok:
        _recording_local["value"] = False
    return ok


def is_recording() -> bool:
    # Priorité au service (peut avoir été synchronisé via OBS).
    try:
        st = _service.get_status(force=False)
        if st.connected:
            return bool(st.recording)
    except Exception:
        pass
    return _recording_local["value"]


def get_obs_status() -> dict:
    st = _service.get_status(force=False)
    return {
        "running": st.running,
        "connected": st.connected,
        "recording": bool(st.recording),
        "scene": st.scene,
        "version": st.version,
        "websocket_version": st.websocket_version,
        "obs_exe_path": st.obs_exe_path,
        "output_dir": st.output_dir,
    }


def configure(host=None, port=None, password=None):
    _service.configure(host=host, port=port, password=password)
    # Réinitialise l'état local d'enregistrement
    _recording_local["value"] = False


def set_preferred_exe(path: str):
    _service.set_preferred_exe(path)


def discover_installations():
    return _service.discover_installations()


def get_recording_path() -> str | None:
    return _service.get_recording_path()
