# file_manager.py
"""
RecordingManager — renommage robuste et nettoyage.

Fonctions :
  * wait_for_finalized_recording(folder, since_ts) :
    attend qu'OBS ait libéré le fichier et que sa taille soit stable.
  * rename_recording(folder, ...) : renomme en Valorant_<date>_<map>_<agent>_<score>_<result>.<ext>
  * clean_old_recordings(folder, max_size_gb)

Garde les signatures historiques pour ne casser ni le moteur d'origine
ni la base de tests existante.
"""
from __future__ import annotations

import os
import re
import glob
import time
import threading
from datetime import datetime
from typing import Optional, Tuple

import logger


_OBS_EXTENSIONS = (".mp4", ".mkv", ".mov", ".flv", ".ts", ".m3u8")


def get_latest_file(directory, extensions=_OBS_EXTENSIONS, since_ts: float = 0.0,
                   exclude: Optional[str] = None):
    """Retourne le fichier d'enregistrement le plus récent du dossier,
    créé après `since_ts` (epoch). Si `exclude` est fourni, ce fichier
    est ignoré (utile pour ne pas renommer deux fois le même fichier)."""
    if not directory:
        return None
    try:
        if not os.path.isdir(directory):
            return None
    except OSError:
        return None
    candidates = []
    for ext in extensions:
        try:
            candidates.extend(glob.glob(os.path.join(directory, f"*{ext}")))
        except (OSError, ValueError):
            continue
    if exclude:
        try:
            ex_abs = os.path.abspath(exclude)
        except (OSError, ValueError):
            ex_abs = exclude
        candidates = [c for c in candidates if os.path.abspath(c) != ex_abs]
    if since_ts > 0:
        candidates = [c for c in candidates if _safe_getctime(c) >= since_ts - 5]
    if not candidates:
        return None
    try:
        return max(candidates, key=_safe_getctime)
    except (OSError, ValueError):
        return None


def _safe_getctime(path: str) -> float:
    try:
        return os.path.getctime(path)
    except OSError:
        return 0.0


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def _is_file_locked(path: str) -> bool:
    """Best-effort : essaie d'ouvrir en append. Windows refusera si OBS
    tient encore le fichier. Sur les autres OS, retourne False."""
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, "ab") as f:
            return False
    except Exception:
        return True


def wait_for_finalized_recording(directory, since_ts: float,
                                 timeout: float = 20.0,
                                 stable_seconds: float = 2.0,
                                 poll: float = 0.4) -> Optional[str]:
    """
    Attend qu'un enregistrement OBS soit finalisé :
      * le fichier existe
      * sa taille est stable pendant `stable_seconds`
      * le fichier n'est plus verrouillé (best effort)
    """
    deadline = time.time() + timeout
    last_size = -1
    stable_since = 0.0
    while time.time() < deadline:
        latest = get_latest_file(directory, since_ts=since_ts)
        if latest and os.path.exists(latest):
            size = _file_size(latest)
            if size == last_size and size > 0:
                if stable_since == 0.0:
                    stable_since = time.time()
                if (time.time() - stable_since) >= stable_seconds and not _is_file_locked(latest):
                    return latest
            else:
                stable_since = 0.0
                last_size = size
        time.sleep(poll)
    return get_latest_file(directory, since_ts=since_ts)


def rename_recording(obs_folder, map_name, agent, score, result) -> Optional[str]:
    """Renomme l'enregistrement OBS le plus récent selon le modèle historique."""
    if not obs_folder or not os.path.isdir(obs_folder):
        logger.warning(f"Dossier OBS introuvable : {obs_folder}")
        return None
    latest = get_latest_file(obs_folder)
    if not latest:
        logger.warning("Aucun fichier d'enregistrement trouvé à renommer.")
        return None
    date_str = datetime.now().strftime("%Y-%m-%d_%Hh%M")
    ext = os.path.splitext(latest)[1]
    safe_map = _safe(map_name)
    safe_agent = _safe(agent)
    new_name = f"Valorant_{date_str}_{safe_map}_{safe_agent}_{score}_{result}{ext}"
    new_path = os.path.join(obs_folder, new_name)
    if os.path.abspath(latest) == os.path.abspath(new_path):
        return new_path
    try:
        if os.path.exists(new_path):
            base, e = os.path.splitext(new_path)
            i = 1
            while os.path.exists(f"{base}_{i}{e}"):
                i += 1
            new_path = f"{base}_{i}{e}"
        os.rename(latest, new_path)
        logger.success(f"Fichier renommé en : {os.path.basename(new_path)}")
        return new_path
    except Exception as e:
        logger.error(f"Impossible de renommer le fichier : {e}")
        return latest


def rename_recording_with_options(obs_folder, map_name, agent, score, result,
                                  template: str = "Valorant_{date}_{map}_{agent}_{score}_{result}",
                                  file_path: Optional[str] = None) -> Tuple[Optional[str], int]:
    """Variante paramétrable qui retourne (new_path, file_size_bytes)."""
    target = file_path or get_latest_file(obs_folder)
    if not target or not os.path.isfile(target):
        return None, 0
    date_str = datetime.now().strftime("%Y-%m-%d_%Hh%M")
    safe_map = _safe(map_name)
    safe_agent = _safe(agent)
    ext = os.path.splitext(target)[1]
    try:
        new_name = template.format(date=date_str, map=safe_map,
                                   agent=safe_agent, score=score, result=result)
    except Exception:
        new_name = f"Valorant_{date_str}_{safe_map}_{safe_agent}_{score}_{result}"
    if not new_name.endswith(ext):
        new_name += ext
    new_path = os.path.join(obs_folder, new_name)
    try:
        if os.path.exists(new_path):
            base, e = os.path.splitext(new_path)
            i = 1
            while os.path.exists(f"{base}_{i}{e}"):
                i += 1
            new_path = f"{base}_{i}{e}"
        os.rename(target, new_path)
        size = _file_size(new_path)
        return new_path, max(0, size)
    except Exception as e:
        logger.error(f"Impossible de renommer le fichier : {e}")
        return target, _file_size(target)


def clean_old_recordings(directory, max_size_gb, protected_paths: Optional[list] = None):
    """Supprime les fichiers les plus anciens si la taille du dossier dépasse
    max_size_gb. Les fichiers dont le chemin est dans `protected_paths`
    ne sont jamais supprimés (utile pour le fichier en cours)."""
    if not directory or max_size_gb <= 0:
        return
    if not os.path.isdir(directory):
        return
    max_size_bytes = int(max_size_gb * 1024 * 1024 * 1024)
    files = []
    for ext in ("*.mp4", "*.mkv", "*.mov"):
        try:
            files.extend(glob.glob(os.path.join(directory, ext)))
        except (OSError, ValueError):
            continue
    if not files:
        return
    # Exclut les fichiers protégés (ex: enregistrement en cours).
    if protected_paths:
        prot_abs = set()
        for p in protected_paths:
            if not p:
                continue
            try:
                prot_abs.add(os.path.abspath(p))
            except (OSError, ValueError):
                continue
        files = [f for f in files if os.path.abspath(f) not in prot_abs]
    if not files:
        return
    files.sort(key=os.path.getmtime)
    total_size = sum(_file_size(f) for f in files)
    if total_size <= max_size_bytes:
        return
    logger.info(f"Espace disque dépassé ({total_size / (1024**3):.2f} Go). Nettoyage en cours…")
    for f in files:
        if total_size <= max_size_bytes:
            break
        size = _file_size(f)
        try:
            os.remove(f)
            total_size -= size
            logger.info(f"Fichier supprimé pour libérer de l'espace : {os.path.basename(f)}")
        except Exception as e:
            logger.error(f"Erreur lors de la suppression de {f} : {e}")


def _safe(value: Optional[str]) -> str:
    if not value:
        return "Inconnu"
    s = str(value).strip()
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"\s+", " ", s)
    return s[:60] or "Inconnu"
