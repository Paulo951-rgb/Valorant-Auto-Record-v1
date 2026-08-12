Valorant Auto Record v1
Résumé

Valorant Auto Record v1 est un outil Python qui automatise l'enregistrement vidéo (ou capture) lors de sessions de jeu Valorant. Il surveille l'exécution du jeu et démarre/arrête l'enregistrement selon des règles définies (par ex. lancement/fermeture de Valorant, ou événements en jeu).
Objectif : permettre d'enregistrer automatiquement des parties sans intervention manuelle.
Table des matières

Contexte
Fonctionnalités
Prérequis
Installation
Configuration
Utilisation
Structure du projet (haut niveau)
Fichiers importants
Dépannage
Sécurité & respect de la vie privée
Licence
Contribuer
Questions fréquentes (FAQ)
Contexte

Ce projet vise à simplifier la capture des parties en lançant automatiquement un enregistreur quand Valorant s'exécute. Utile pour les créateurs, streamers, et joueurs souhaitant conserver automatiquement leurs parties.
Fonctionnalités (attendues)

Détection automatique du processus Valorant.
Démarrage et arrêt automatiques d'une session d'enregistrement.
Sauvegarde des vidéos dans un dossier de sortie organisé par date/heure.
Option de config (dossier de sortie, format, durée max, nommage).
Fichiers journaux (logs) pour diagnostiquer le comportement.
Mode test / simulation (exécuter sans réellement enregistrer).
Option pour regrouper/compresser les sorties (bundle).
Prérequis

Système d'exploitation : Windows (majoritairement attendu pour Valorant), mais le code Python peut fonctionner sur Linux si adapté.
Python 3.8+ recommandé.
Droits suffisants pour capturer l'écran / accéder au périphérique d'enregistrement.
Logiciels d'enregistrement (si le projet s'appuie sur un enregistreur externe comme OBS) : OBS + obs-websocket, ou dépendances Python (p. ex. pyautogui, ffmpeg).
ffmpeg si l'enregistrement/encodage est géré localement.
Installation (rapide)

Cloner le dépôt :
git clone https://github.com/Paulo951-rgb/Valorant-Auto-Record-v1.git
cd Valorant-Auto-Record-v1
Créer un environnement virtuel :
python -m venv venv
Windows : venv\Scripts\activate
macOS/Linux : source venv/bin/activate
Installer les dépendances :
pip install -r requirements.txt
Si le dépôt n'a pas de requirements.txt, installer les dépendances nécessaires (ex. psutil, watchdog, requests, pywin32, ffmpeg-python) selon la doc fournie.
Installer ffmpeg (si nécessaire) et s'assurer que ffmpeg est dans le PATH.
Configuration

Le projet devrait proposer un fichier de configuration (ex. config.json, config.yaml ou variables d'environnement). Par défaut, voici les paramètres courants :
output_dir : dossier de sortie des vidéos (ex. ./recordings)
record_format : mp4 / mkv
max_file_size / max_duration : pour découper les enregistrements
monitor_process_name : nom du processus Valorant (ex. "VALORANT.exe")
use_obs : true/false — si utilise OBS via websocket
obs_address / obs_password : configuration OBS
log_level : DEBUG / INFO / WARNING / ERROR
Si le dépôt n'inclut pas de fichier config, créer un fichier config.json exemple : { "output_dir": "recordings", "record_format": "mp4", "monitor_process_name": "VALORANT.exe", "use_obs": false, "max_duration_minutes": 60, "log_level": "INFO" }
Utilisation

Mode simple (exemples) :
python main.py
python main.py --config config.json
python main.py --dry-run (simule le comportement sans enregistrer)
Exécuter en arrière-plan (Windows) : créer un service, une tâche planifiée, ou lancer dans un terminal et laisser tourner.
Voir les logs (fichier logs/app.log ou sortie console) pour savoir quand l'enregistrement commence/arrête.
Structure du projet (haut niveau)

main.py — point d'entrée (surveille le jeu et orchestre l'enregistrement)
recorder/ — code d'enregistrement (wrapper ffmpeg ou contrôleur OBS)
watcher/ — surveille les processus et détecte Valorant
config/ — exemples de configuration
logs/ — journaux d'exécution
recordings/ — sortie par défaut pour les vidéos
README.md — ce fichier Remarque : noms et arborescence peuvent varier : adapte en fonction du contenu réel.
Fichiers importants

README.md — documentation
requirements.txt — dépendances Python
config.example.json — exemple de configuration
main.py — script principal
