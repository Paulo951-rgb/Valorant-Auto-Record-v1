# obs_controller.py
import time
import subprocess
import os
from obsws_python import ReqClient

import config
import logger

# État actuel de l'enregistrement
recording = False

# Client OBS réutilisé
_client = None

DEFAULT_OBS_PATH = r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"

# ============================================================
# VÉRIFICATION ET DÉMARRAGE PROCESSUS OBS
# ============================================================

def is_obs_running():
    """Vérifie si le processus OBS est en cours d'exécution."""
    try:
        import psutil
        for proc in psutil.process_iter(['name']):
            name = (proc.info.get('name') or '').lower()
            if name == 'obs64.exe':
                return True
    except ImportError:
        # Solution de secours si psutil n'est pas installé
        try:
            output = subprocess.check_output('tasklist', shell=True).decode(errors='ignore')
            return "obs64.exe" in output.lower()
        except Exception:
            return False
    return False


def launch_obs():
    """Démarre le processus OBS si ce dernier est introuvable."""
    if is_obs_running():
        logger.info("OBS est déjà en cours d'exécution.")
        return True

    obs_path = getattr(config, "OBS_EXE_PATH", DEFAULT_OBS_PATH)
    if os.path.exists(obs_path):
        logger.info("Démarrage d'OBS Studio...")
        working_dir = os.path.dirname(obs_path)
        subprocess.Popen([obs_path], cwd=working_dir)
        time.sleep(5)  # Attente de l'initialisation du WebSocket d'OBS
        return True
    else:
        logger.error(f"Impossible de démarrer OBS automatiquement. Fichier introuvable : {obs_path}")
        return False

# ============================================================
# CONNEXION OBS WEBSOCKET
# ============================================================

def connect_obs():
    global _client

    if _client is not None:
        try:
            _client.get_version()
            return _client
        except Exception:
            logger.warning("Connexion OBS perdue, reconnexion...")
            _client = None

    for attempt in range(1, config.OBS_MAX_RETRY + 1):
        try:
            logger.info(f"Tentative connexion OBS {attempt}/{config.OBS_MAX_RETRY}")
            client = ReqClient(
                host=config.OBS_HOST,
                port=config.OBS_PORT,
                password=config.OBS_PASSWORD
            )
            logger.success("Connexion OBS réussie")
            _client = client
            return _client
        except Exception as e:
            logger.error("Connexion OBS impossible", e)
            if attempt < config.OBS_MAX_RETRY:
                time.sleep(config.OBS_RETRY_DELAY)

    logger.error("Impossible de joindre OBS après plusieurs tentatives")
    return None


def obs_available():
    return connect_obs() is not None


def test_obs_connection():
    """Vérifie si la connexion avec OBS est disponible."""
    return obs_available()

# ============================================================
# ENREGISTREMENT
# ============================================================

def start_record():
    global recording
    if recording:
        logger.warning("StartRecord ignoré : déjà en enregistrement")
        return False

    obs = connect_obs()
    if obs is None:
        return False

    try:
        obs.start_record()
        recording = True
        logger.success("ENREGISTREMENT OBS DEMARRE")
        return True
    except Exception as e:
        logger.error("Erreur pendant StartRecord", e)
        return False


def stop_record():
    global recording
    if not recording:
        logger.warning("StopRecord ignoré : aucun enregistrement actif")
        return False

    obs = connect_obs()
    if obs is None:
        return False

    try:
        obs.stop_record()
        recording = False
        logger.success("ENREGISTREMENT OBS ARRETE")
        return True
    except Exception as e:
        logger.error("Erreur pendant StopRecord", e)
        return False


def is_recording():
    return recording


def get_obs_status():
    """Retourne un instantané léger de l'état d'OBS (non bloquant).

    Contrairement à connect_obs(), cette fonction NE tente pas de reconnecter
    si la connexion est perdue : elle se contente de vérifier le client mis en
    cache. Les reconnexions lourdes sont laissées à connect_obs() (appelée par
    start_record / test_obs_connection).
    """
    running = is_obs_running()
    connected = False
    scene = None
    if _client is not None:
        try:
            _client.get_version()
            connected = True
            try:
                scene = _client.get_current_program_scene().scene_name
            except Exception:
                scene = None
        except Exception:
            connected = False
    return {
        "running": running,
        "connected": connected,
        "recording": recording,
        "scene": scene,
    }