# valorant_api.py — shim rétro-compatible
# Toute la logique a été déplacée dans valorant_service.ValorantDataService.

import logger
from valorant_service import (
    ValorantDataService,
    RiotClientNotRunning,
    MAP_MAPPING,
    AGENT_MAPPING,
    LOCKFILE_PATH,
    SessionState,
    RECORDABLE_STATES,
)

_service = ValorantDataService()


def get_current_state(debug=False):
    """API historique : renvoie {"state","map","agent","score"}."""
    try:
        s = _service.get_current_session(use_cache=False)
        return {
            "state": s.state,
            "map": s.map,
            "agent": s.agent,
            "score": s.score,
        }
    except RiotClientNotRunning:
        raise
    except Exception as e:
        if debug:
            print(f"Erreur get_current_state : {e}")
        return {"state": "MENUS", "map": "Inconnu", "agent": "Inconnu", "score": "0-0"}


def read_lockfile():
    return _service._read_lockfile()  # noqa: SLF001


def get_own_puuid(port, password):
    return _service._get_own_puuid(port, password)  # noqa: SLF001
