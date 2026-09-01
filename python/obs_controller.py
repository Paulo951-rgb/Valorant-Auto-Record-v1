# obs_controller.py — shim rétro-compatible
# Toute la logique a été déplacée dans obs_service.OBSService.

import config
import logger
from obs_service import (
    OBSService,
    ObsStatus,
    ObsInstallation,
    discover as _discover,
    is_obs_process_running,
    OBSWS_AVAILABLE,
)

_service: OBSService = OBSService()


def _get_client():
    return _service._client  # noqa: SLF001


def is_obs_running() -> bool:
    """Indique si un processus OBS est en cours (peu importe le chemin)."""
    return is_obs_process_running()


def launch_obs() -> bool:
    """Démarre OBS et attend la disponibilité du WebSocket."""
    return _service.launch(timeout=config.OBS_LAUNCH_TIMEOUT_S)


def connect_obs():
    """Renvoie le client OBS (ou None). Connexion paresseuse."""
    return _service._client if _service.connect() else None  # noqa: SLF001


def obs_available() -> bool:
    return _service.connect()


def test_obs_connection() -> bool:
    return _service.connect()


def start_record() -> bool:
    return _service.start_recording()


def stop_record() -> bool:
    return _service.stop_recording()


def is_recording() -> bool:
    """Indique si OBS enregistre réellement. Si OBS est connecté, on
    privilégie l'état distant ; sinon on retombe sur l'état local."""
    try:
        st = _service.get_status(force=False)
        if st.connected:
            return bool(st.recording)
    except Exception:
        pass
    return _service._local_state_recording  # noqa: SLF001


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
    # Force la reconnexion lors du prochain appel.
    _service.reset_client()


def set_preferred_exe(path: str):
    _service.set_preferred_exe(path)


def discover_installations():
    return _service.discover_installations()


def get_recording_path():
    return _service.get_recording_path()
