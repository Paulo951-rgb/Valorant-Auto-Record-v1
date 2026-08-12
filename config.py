# ============================================================
# CONFIGURATION - VALORANT AUTO RECORD OBS
# ============================================================


# ============================================================
# OBS WEBSOCKET
# ============================================================

OBS_HOST = "localhost"
OBS_PORT = 4455

# Mot de passe OBS WebSocket
# OBS Studio > Outils > Paramètres du serveur WebSocket
OBS_PASSWORD = "valorant123"


# ============================================================
# COMPORTEMENT
# ============================================================

# Intervalle entre deux vérifications de l'état de la partie
# (via l'API locale du client Riot), en secondes.
POLL_INTERVAL = 2

# Affiche des infos détaillées sur ce que renvoie l'API Riot
# (utile pour diagnostiquer). Remets à True si besoin de déboguer.
DEBUG = False


# ============================================================
# OPTIONS AVANCEES OBS
# ============================================================

# Nombre maximum de tentatives de connexion OBS
OBS_MAX_RETRY = 5

# Temps entre chaque tentative OBS (secondes)
OBS_RETRY_DELAY = 2