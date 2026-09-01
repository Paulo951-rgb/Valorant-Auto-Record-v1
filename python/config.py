# config.py
"""
Constantes du moteur. Les valeurs utilisateur sont stockées dans
config_local.json (ConfigStore) et écrasées à chaud au démarrage.
"""

OBS_HOST = "localhost"
OBS_PORT = 4455
OBS_PASSWORD = ""

POLL_INTERVAL = 2
DEBUG = False

OBS_MAX_RETRY = 5
OBS_RETRY_DELAY = 2
OBS_LAUNCH_TIMEOUT_S = 45
OBS_LAUNCH_POLL_S = 0.75

VALORANT_PROCESS_NAMES = ("valorant.exe", "valorant-win64-shipping.exe")
RIOT_PROCESS_NAMES = ("riotclient.exe", "riotclientservices.exe")

# Dossier de sortie par défaut si OBS ne peut pas être interrogé.
DEFAULT_OBS_FOLDER = "~"

# Token d'identification de match généré localement quand l'API Riot ne
# fournit pas d'identifiant. Préfixe 'local-' pour le distinguer d'un
# matchId Riot.
LOCAL_MATCH_PREFIX = "local-"
