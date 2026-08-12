import os
import glob
import time
from datetime import datetime
import logger

def get_latest_file(directory, extensions=(".mp4", ".mkv")):
    """Retourne le fichier le plus récent dans le dossier cible."""
    list_of_files = []
    for ext in extensions:
        list_of_files.extend(glob.glob(os.path.join(directory, f"*{ext}")))
    
    if not list_of_files:
        return None
    return max(list_of_files, key=os.path.getctime)

def rename_recording(obs_folder, map_name, agent, score, result):
    """Attend la fin de l'écriture d'OBS, renomme le fichier et renvoie le nouveau chemin."""
    latest_file = get_latest_file(obs_folder)
    if not latest_file:
        logger.warning("Aucun fichier d'enregistrement trouvé à renommer.")
        return None

    # Petite pause pour s'assurer qu'OBS a bien libéré le fichier
    time.sleep(2)

    # Préparation du nouveau nom
    date_str = datetime.now().strftime("%Y-%m-%d_%Hh%M")
    extension = os.path.splitext(latest_file)[1]
    new_name = f"Valorant_{date_str}_{map_name}_{agent}_{score}_{result}{extension}"
    new_path = os.path.join(obs_folder, new_name)

    try:
        os.rename(latest_file, new_path)
        logger.success(f"Fichier renommé en : {new_name}")
        return new_path
    except Exception as e:
        logger.error(f"Impossible de renommer le fichier : {e}")
        return latest_file

def clean_old_recordings(directory, max_size_gb):
    """Supprime les fichiers les plus anciens si la taille du dossier dépasse max_size_gb."""
    max_size_bytes = max_size_gb * 1024 * 1024 * 1024
    files = glob.glob(os.path.join(directory, "*.mp4")) + glob.glob(os.path.join(directory, "*.mkv"))
    
    # Trier les fichiers du plus ancien au plus récent
    files.sort(key=os.path.getmtime)
    
    total_size = sum(os.path.getsize(f) for f in files)
    
    if total_size <= max_size_bytes:
        return

    logger.info(f"Espace disque dépassé ({total_size / (1024**3):.2f} Go). Nettoyage en cours...")
    
    for f in files:
        if total_size <= max_size_bytes:
            break
        file_size = os.path.getsize(f)
        try:
            os.remove(f)
            total_size -= file_size
            logger.info(f"Fichier supprimé pour libérer de l'espace : {os.path.basename(f)}")
        except Exception as e:
            logger.error(f"Erreur lors de la suppression de {f} : {e}")