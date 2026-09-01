# match_repository.py
"""
MatchRepository — accès SQLite aux matchs enregistrés.

Schéma cible (table `matches`) :
  id INTEGER PRIMARY KEY AUTOINCREMENT
  match_id TEXT UNIQUE            -- ID Riot ou 'local-<uuid>'
  date TEXT NOT NULL              -- ISO 8601
  map_name TEXT
  agent TEXT
  score TEXT
  ally_score INTEGER
  enemy_score INTEGER
  result TEXT                     -- Victoire / Defaite / Inconnu
  mode TEXT                       -- queueId ou label
  duration_seconds INTEGER
  kills INTEGER
  deaths INTEGER
  assists INTEGER
  kd REAL
  rr_change INTEGER
  rank TEXT
  video_path TEXT
  file_size_bytes INTEGER
  status TEXT                     -- completed / recording / failed
  created_at TEXT

L'ancienne table `matches` (id, date, map_name, agent, score, result,
video_path) est conservée par migration : nouvelles colonnes ajoutées
avec DEFAULT NULL.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

import config
import logger

DB_NAME = "valorant_recorder.db"

# Liste ordonnée des colonnes cibles. Utilisée pour SELECT et INSERT.
COLUMNS: List[str] = [
    "id", "match_id", "date", "map_name", "agent", "score",
    "ally_score", "enemy_score", "result", "mode", "duration_seconds",
    "kills", "deaths", "assists", "kd", "rr_change", "rank",
    "video_path", "file_size_bytes", "status", "created_at",
]

# Anciennes colonnes (avant migration). Conservées pour rétro-compat.
LEGACY_COLUMNS = ["id", "date", "map_name", "agent", "score", "result", "video_path"]


class MatchRepository:
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or DB_NAME
        self._lock = threading.RLock()
        self._init_done = False

    # ----------------- lifecycle -----------------
    def init(self):
        with self._lock:
            self._create_and_migrate()
            self._init_done = True

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self._db_path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA foreign_keys=ON;")
        try:
            yield c
        finally:
            c.close()

    def _create_and_migrate(self):
        with self._conn() as c:
            cur = c.cursor()
            # Base : ancien schéma d'abord (pour gérer la migration ascendante).
            cur.execute(
                """CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    map_name TEXT,
                    agent TEXT,
                    score TEXT,
                    result TEXT,
                    video_path TEXT
                )"""
            )
            # Récupération de la structure réelle.
            cur.execute("PRAGMA table_info(matches)")
            existing = {row["name"] for row in cur.fetchall()}
            # Migrations ALTER TABLE idempotentes.
            for col, decl in [
                ("match_id", "TEXT"),
                ("ally_score", "INTEGER"),
                ("enemy_score", "INTEGER"),
                ("mode", "TEXT"),
                ("duration_seconds", "INTEGER"),
                ("kills", "INTEGER"),
                ("deaths", "INTEGER"),
                ("assists", "INTEGER"),
                ("kd", "REAL"),
                ("rr_change", "INTEGER"),
                ("rank", "TEXT"),
                ("file_size_bytes", "INTEGER"),
                ("status", "TEXT"),
                ("created_at", "TEXT"),
            ]:
                if col not in existing:
                    try:
                        cur.execute(f"ALTER TABLE matches ADD COLUMN {col} {decl}")
                        logger.info(f"DB : colonne '{col}' ajoutée.")
                    except Exception as e:
                        logger.warning(f"DB : impossible d'ajouter la colonne {col} : {e}")
            # Index utiles
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date)")
            except Exception:
                pass
            try:
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_match_id ON matches(match_id)")
            except Exception:
                pass

    # ----------------- CRUD -----------------
    def upsert_match(self, data: Dict[str, Any]) -> int:
        """Insère ou met à jour un match par match_id. Retourne l'id."""
        with self._lock:
            self._ensure()
            data = dict(data)
            match_id = data.get("match_id")
            if not match_id:
                match_id = f"{config.LOCAL_MATCH_PREFIX}{uuid.uuid4().hex[:12]}"
                data["match_id"] = match_id
            with self._conn() as c:
                cur = c.cursor()
                cur.execute("SELECT id FROM matches WHERE match_id=?", (match_id,))
                row = cur.fetchone()
                fields = [c for c in COLUMNS if c not in ("id",)]
                if row is None:
                    placeholders = ",".join(["?"] * len(fields))
                    cols = ",".join(fields)
                    values = [data.get(f) for f in fields]
                    cur.execute(
                        f"INSERT INTO matches ({cols}) VALUES ({placeholders})",
                        values,
                    )
                    return int(cur.lastrowid or 0)
                else:
                    sets = ",".join(f"{f}=?" for f in fields)
                    values = [data.get(f) for f in fields]
                    values.append(match_id)
                    cur.execute(
                        f"UPDATE matches SET {sets} WHERE match_id=?",
                        values,
                    )
                    return int(row["id"])

    def get_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure()
            with self._conn() as c:
                cur = c.cursor()
                cur.execute("SELECT * FROM matches ORDER BY id DESC")
                return [dict(r) for r in cur.fetchall()]

    def get_by_match_id(self, match_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._ensure()
            with self._conn() as c:
                cur = c.cursor()
                cur.execute("SELECT * FROM matches WHERE match_id=?", (match_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def delete(self, match_id: str) -> bool:
        with self._lock:
            self._ensure()
            with self._conn() as c:
                cur = c.cursor()
                cur.execute("DELETE FROM matches WHERE match_id=?", (match_id,))
                return cur.rowcount > 0

    def count(self) -> int:
        with self._lock:
            self._ensure()
            with self._conn() as c:
                cur = c.cursor()
                cur.execute("SELECT COUNT(*) AS c FROM matches")
                return int(cur.fetchone()["c"])

    # ----------------- helpers -----------------
    def _ensure(self):
        if not self._init_done:
            self.init()


# Compatibilité ascendante (les anciens modules appelaient ces fonctions).
_DEFAULT_REPO: Optional[MatchRepository] = None


def init_db():
    global _DEFAULT_REPO
    _DEFAULT_REPO = MatchRepository()
    _DEFAULT_REPO.init()


def add_match(date, map_name, agent, score, result, video_path) -> int:
    if _DEFAULT_REPO is None:
        init_db()
    return _DEFAULT_REPO.upsert_match({
        "date": date,
        "map_name": map_name,
        "agent": agent,
        "score": score,
        "result": result,
        "video_path": video_path,
        "status": "completed",
    })


def get_all_matches() -> List[tuple]:
    if _DEFAULT_REPO is None:
        init_db()
    rows = _DEFAULT_REPO.get_all()
    return [_row_to_legacy_tuple(r) for r in rows]


def _row_to_legacy_tuple(row: Dict[str, Any]) -> tuple:
    return (
        row.get("id"),
        row.get("date"),
        row.get("map_name"),
        row.get("agent"),
        row.get("score"),
        row.get("result"),
        row.get("video_path"),
    )
