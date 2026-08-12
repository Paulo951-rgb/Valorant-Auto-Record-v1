# Valorant Auto Record

Enregistrement **automatique** de vos parties Valorant via OBS Studio, dans une véritable application de bureau **Electron** avec interface graphique moderne.

Le moteur Python d'origine (détection Valorant / Riot, connexion OBS, démarrage/arrêt de l'enregistrement, renommage des fichiers, historique) a été **conservé intégralement**. Electron vient s'ajouter autour de ce moteur pour fournir un tableau de bord, des contrôles, des paramètres et des logs en temps réel.

---

## Sommaire

- [Présentation](#présentation)
- [Fonctionnalités](#fonctionnalités)
- [Architecture](#architecture)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Installation & configuration d'OBS](#installation--configuration-dobs)
- [Lancement en développement](#lancement-en-développement)
- [Création de l'exécutable Windows (.exe)](#création-de-lexécutable-windows-exe)
- [Configuration](#configuration)
- [Fonctionnement](#fonctionnement)
- [Dépannage](#dépannage)
- [Sécurité](#sécurité)
- [Licence](#licence)

---

## Présentation

Valorant Auto Record surveille le lancement de Valorant et de Riot, détecte le début et la fin d'une partie, puis démarre/arrête automatiquement l'enregistrement OBS. À la fin du match, la vidéo est renommée avec la carte, l'agent, le score et le résultat, puis est ajoutée à l'historique.

Le logiciel reste actif en arrière-plan et se reconnecte automatiquement à OBS / Riot si besoin.

## Fonctionnalités

- Détection automatique d'OBS, de Valorant et du client Riot.
- Connexion à OBS via OBS WebSocket (logique existante conservée).
- Récupération de l'état de session Valorant via l'API locale Riot (lockfile).
- Démarrage/arrêt **automatique** de l'enregistrement au début/fin de partie.
- Démarrage/arrêt **manuel** depuis l'interface.
- Renommage des fichiers : `Valorant_<date>_<carte>_<agent>_<score>_<résultat>.mp4`.
- Historique des matchs (base SQLite).
- Nettoyage automatique des anciens enregistrements (seuil configurable en Go).
- Interface Electron : **Tableau de bord**, **Historique**, **Logs**, **Paramètres**.
- Statut en temps réel (OBS, Valorant, Riot, partie, enregistrement, durée).
- Logs horodatés avec niveaux INFO / SUCCESS / WARNING / ERROR / DEBUG, sauvegardés dans `logs/app.log`.
- Reconnexion automatique d'OBS, relance automatique du backend en cas de crash.
- Paramètres persistés après redémarrage (`python/config_local.json`).

## Architecture

```
Valorant Auto Record.exe (Electron)
│
├── Renderer (interface HTML/CSS/JS)
│   ├── Tableau de bord (statuts temps réel + contrôles)
│   ├── Historique (matchs enregistrés)
│   ├── Logs (journal horodaté)
│   └── Paramètres (configuration persistée)
│
├── Main Process Node.js (electron/)
│   ├── main.js          → fenêtre + IPC + cycle de vie
│   ├── preload.js       → pont sécurisé (contextBridge)
│   └── backend_bridge.js → spawn du backend Python + protocole JSON-lines
│
└── Backend Python (python/) — moteur EXISTANT conservé
    ├── backend.py        → point d'entrée JSON-lines (stdin/stdout)
    ├── monitor.py        → boucle de surveillance extraite de main.py
    ├── obs_controller.py → connexion OBS WebSocket + record (inchangé)
    ├── valorant_api.py   → lockfile Riot + état session (inchangé)
    ├── database.py       → historique SQLite (inchangé)
    ├── file_manager.py   → renommage + nettoyage (inchangé)
    ├── logger.py         → logs (inchangé)
    ├── config.py         → constantes (inchangé)
    └── main.py           → ancienne interface customtkinter (alternative autonome)
```

**Communication Electron ↔ Python :** protocole **JSON-lines** sur `stdin`/`stdout`. Le backend reste un processus unique longue durée (pas de relancement toutes les quelques secondes). Electron envoie des requêtes `{"id","method","params"}` et reçoit des réponses + des notifications temps réel (`status`, `log`, `ready`, `match_started`, `match_ended`, ...).

## Prérequis

- **Windows 10/11** (Valorant est exclusivement Windows).
- **OBS Studio** avec le plugin **OBS WebSocket** activé (inclus dans OBS ≥ 28).
- **Python 3.8+** installé et accessible dans le PATH (mode développement / build avec `.py`).
  - Optionnel mais recommandé pour un `.exe` autonome : **PyInstaller** (voir [Build Windows](#création-de-lexécutable-windows-exe)).
- **Node.js 18+** et **npm** (pour développer/recompiler l'interface).
- Valorant et le Riot Client installés.

## Installation

```bash
git clone https://github.com/Paulo951-rgb/Valorant-Auto-Record-v1.git
cd Valorant-Auto-Record-v1

# Dépendances Node (Electron + electron-builder)
npm install

# Dépendances Python du moteur
pip install -r requirements.txt
```

> `requirements.txt` contient : `obsws-python`, `requests`, `colorama`, `psutil`.

## Installation & configuration d'OBS

1. Installez **OBS Studio** (version 28+ : OBS WebSocket est intégré).
2. Dans OBS : **Outils → Paramètres du serveur WebSocket**.
3. Activez le serveur. Notez :
   - le **port** (par défaut **4455**),
   - le **mot de passe** (généré, ou personnalisez-le).
4. Dans l'application : **Paramètres → OBS WebSocket** → renseignez l'adresse (`localhost`), le port et le mot de passe.

## Lancement en développement

```bash
npm start        # lance l'application Electron
npm run dev      # idem + DevTools ouverts
```

Au démarrage, l'application :
1. ouvre la fenêtre,
2. lance automatiquement le backend Python (`python/backend.py`),
3. récupère l'état initial et l'affiche.

## Création de l'exécutable Windows (.exe)

```bash
npm run build:win
```

Le résultat est dans `dist/` : un installeur NSIS (`Valorant-Auto-Record-Setup-1.0.0.exe`).

Le dossier `python/` est inclus comme `extraResources`, donc le `.exe` peut lancer `backend.py` avec l'interpréteur Python système. Pour un `.exe` 100 % autonome (sans Python installé sur le PC cible), compilez le backend avec **PyInstaller** :

```bash
cd python
pip install pyinstaller
pyinstaller --onefile --name backend backend.py
# placez dist/backend.exe dans python/ avant `npm run build:win`
```

L'application détecte automatiquement `backend.exe` s'il est présent et l'utilise à la place de `python backend.py`.

## Configuration

Tous les paramètres sont éditables dans **Paramètres** et persistés dans `python/config_local.json` :

| Paramètre | Description | Défaut |
|---|---|---|
| `obs_folder` | Dossier de sauvegarde des vidéos | `~/Videos` |
| `obs_exe_path` | Chemin de `obs64.exe` | `C:\Program Files\obs-studio\bin\64bit\obs64.exe` |
| `obs_host` | Adresse OBS WebSocket | `localhost` |
| `obs_port` | Port OBS WebSocket | `4455` |
| `obs_password` | Mot de passe OBS WebSocket | `valorant123` |
| `poll_interval` | Intervalle de sondage Riot (s) | `2` |
| `auto_record` | Enregistrement automatique | `true` |
| `log_level` | Niveau de logs | `INFO` |
| `max_size_gb` | Nettoyage auto (Go, 0 = illimité) | `50` |
| `max_duration_minutes` | Durée max indicative (min) | `60` |
| `record_format` | Format attendu (mp4/mkv) | `mp4` |
| `file_naming` | Modèle de nommage | `Valorant_{date}_{map}_{agent}_{score}_{result}` |

## Fonctionnement

```
OBS ouvert → Connexion OBS → VALORANT détecté → Récupération Riot
        → Surveillance de l'état → Partie détectée → Démarrage OBS
        → Partie terminée → Arrêt OBS → Renommage → Historique → Nettoyage
```

La logique de surveillance (`python/monitor.py`) reproduit **exactement** la boucle d'origine (`main.py`) : passage à l'état `INGAME` → lancement OBS + `start_record` ; sortie de `INGAME` → `stop_record` + renommage + base de données + nettoyage.

L'interface se met à jour automatiquement via les événements envoyés par le backend (pas besoin de redémarrer). Un rafraîchissement périodique de sécurité (`get_status` toutes les 4 s) est ajouté.

## Dépannage

| Symptôme | Cause / Solution |
|---|---|
| « OBS n'est pas lancé » | Démarrez OBS, ou cliquez sur **Lancer OBS** (vérifiez `obs_exe_path`). |
| « Impossible de se connecter à OBS » | Vérifiez le port/mot de passe dans **Paramètres → OBS WebSocket**. Le logiciel retente automatiquement. |
| « Riot Client non détecté (Valorant éteint) » | Valorant/Riot n'est pas lancé. Lancez le jeu. |
| « Backend déconnecté » | Le backend Python a crashé. Le bouton **Relancer le backend** le redémarre. Vérifiez que Python et `requirements.txt` sont installés. |
| Aucune information de partie | Vérifiez que Valorant tourne et que le Riot Client est connecté. L'API locale nécessite le lockfile Riot. |
| Les vidéos ne sont pas renommées | Vérifiez `obs_folder` : il doit correspondre au **dossier de sortie OBS**. |
| Logs vides | Baissez `log_level` sur `DEBUG`. Les logs sont aussi dans `logs/app.log`. |

## Sécurité

- Le mot de passe OBS est stocké en clair dans `config_local.json` (fichier local, non transmis ailleurs). Ne partagez pas ce fichier.
- Aucun port réseau n'est exposé : la communication Electron ↔ Python se fait via `stdin`/`stdout` uniquement.
- L'API Riot utilisée est strictement locale (`127.0.0.1`) via le lockfile du client.

## Licence

MIT — voir le dépôt d'origine.

> Ce projet est fourni à titre éducatif. Valorant et Riot Games sont des marques de Riot Games. Ce projet n'est pas affilié à Riot Games.
