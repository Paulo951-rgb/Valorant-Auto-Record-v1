import sqlite3
import os

DB_NAME = "valorant_recorder.db"

def init_db():
    """Crée la table des matchs si elle n'existe pas."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            map_name TEXT,
            agent TEXT,
            score TEXT,
            result TEXT,
            video_path TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_match(date, map_name, agent, score, result, video_path):
    """Enregistre un match dans la base de données."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO matches (date, map_name, agent, score, result, video_path)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (date, map_name, agent, score, result, video_path))
    conn.commit()
    conn.close()

def get_all_matches():
    """Récupère l'historique des matchs."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM matches ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows