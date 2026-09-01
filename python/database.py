# database.py — shim rétro-compatible.
# Toute la logique a été déplacée dans match_repository.py.
from match_repository import (
    init_db,
    add_match,
    get_all_matches,
    MatchRepository,
    DB_NAME,
)

__all__ = ["init_db", "add_match", "get_all_matches", "MatchRepository", "DB_NAME"]
