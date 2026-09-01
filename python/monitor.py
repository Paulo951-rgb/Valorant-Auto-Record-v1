# monitor.py — shim rétro-compatible.
# La logique a été déplacée dans game_monitor.py.

from game_monitor import (
    GameMonitor as Monitor,
    GameState,
    is_valorant_running,
    riot_connected_value,
)

# Ancien nom de classe
Monitor = Monitor
