# Valorant Auto Record

Enregistrement **automatique** de vos parties Valorant via OBS Studio, dans une véritable application de bureau **Electron** moderne et simple d'usage.

L'application détecte automatiquement OBS, le lance discrètement si besoin, se connecte au WebSocket, surveille Valorant / le client Riot, démarre l'enregistrement au début de chaque partie, l'arrête à la fin, renomme le fichier et l'ajoute à l'historique — **sans intervention de l'utilisateur**.

---

## Sommaire

- [Philosophie](#philosophie)
- [Fonctionnalités](#fonctionnalités)
- [Architecture](#architecture)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Lancement en développement](#lancement-en-développement)
- [Création de l'exécutable Windows (.exe)](#création-de-lexécutable-windows-exe)
- [Configuration](#configuration)
- [Fonctionnement](#fonctionnement)
- [Dépannage](#dépannage)
- [Tests](#tests)
- [Sécurité](#sécurité)
- [Licence](#licence)

---

## Philosophie

> **L'utilisateur ne devrait presque rien avoir à configurer.**

Au premier lancement, l'application :

- détecte **OBS Studio** (toutes installations : Program Files, Program Files (x86), chemins personnalisés, processus déjà en cours) ;
- détecte **Valorant** et le **Riot Client** ;
- tente la connexion au **WebSocket OBS** automatiquement ;
- récupère le **dossier d'enregistrement réel** depuis OBS (plus besoin de le saisir).

L'utilisateur n'a qu'à lancer Valorant et à jouer.

---

## Fonctionnalités

- **Détection automatique multi-chemin d'OBS Studio** (obs-studio/Program Files, Program Files (x86), chemins personnalisés, processus en cours).
- **Lancement discret d'OBS** : OBS démarre minimisé, sans voler le focus, sans flash visible, sans interrompre le jeu. Si OBS est déjà ouvert avec sa fenêtre visible, elle n'est **pas** masquée.
- **Connexion WebSocket résiliente** avec boucle de disponibilité et timeout/backoff, compatibilité obs-websocket v4 (plugin OBS < 28) et v5 (intégré OBS 28+).
- **Détection robuste Valorant** : distinction Riot Client / Valorant / Agent select / Partie en cours / Fin de partie / Retour menu.
- **Machine à états** pour la surveillance (`IDLE → VALORANT_LAUNCHED → MATCH_LOADING → MATCH_ACTIVE → MATCH_FINISHING → RECORDING_STOPPING → FINALIZING → COMPLETED`).
- **Confirmation réelle** des changements d'état OBS (pas de simple « START_REQUESTED » — on attend que OBS confirme `output_active=true`).
- **Finalisation robuste du fichier** : on attend que la taille du fichier vidéo soit stable et qu'il ne soit plus verrouillé avant de renommer / insérer en base.
- **Identifiant unique de match** : `match_id` Riot quand disponible, sinon `local-<uuid>` clairement distingué.
- **Schéma SQLite évolué** : migration automatique préservant les anciennes données, colonnes `ally_score`, `enemy_score`, `mode`, `duration_seconds`, `kills`, `deaths`, `assists`, `kd`, `rr_change`, `rank`, `file_size_bytes`, `status`, `match_id`.
- **Anti-rebond** : cooldown 30 s pour éviter les doubles enregistrements.
- **Interface sobre** : navigation à 4 onglets (Accueil / Parties / Paramètres / Diagnostics), thème sombre moderne.
- **Page Parties** : cartes par match avec recherche, filtres Victoire/Défaite, tri par date / durée / carte.
- **Paramètres utilisateur simplifiés** avec section « Avancé » masquée par défaut.
- **Tray icon** + **minimisation dans la zone de notification** + **démarrage avec Windows** (via `setLoginItemSettings`).
- **Single-instance** : une seule instance de l'application peut tourner.
- **Reconnexion automatique** d'OBS et relance automatique du backend en cas de crash (backoff).
- **Logs catégorisés** (INFO / SUCCESS / WARNING / ERROR / DEBUG) avec messages humanisés côté utilisateur.
- **Tests unitaires** (21 tests) sur OBS, Valorant, enregistrement, historique, migration, protocole.

---

## Architecture

```
Valorant Auto Record.exe (Electron)
│
├── Renderer (HTML/CSS/JS)
│   ├── Accueil : 4 cartes (Valorant / OBS / Enregistrement / Auto)
│   ├── Parties  : cartes + recherche + filtres + tri
│   ├── Paramètres : Général / Enregistrements / OBS / Avancé
│   └── Diagnostics : statut système, OBS, logs temps réel
│
├── Main Process Node.js (electron/)
│   ├── main.js           → fenêtre + tray + cycle de vie + IPC
│   ├── preload.js        → pont sécurisé (contextBridge)
│   └── backend_bridge.js → spawn du backend + JSON-lines
│
└── Backend Python (python/) — services séparés
    ├── backend.py            → entrée JSON-lines (stdin/stdout)
    ├── obs_service.py        → OBSService : discover/launch/connect/record
    ├── valorant_service.py   → ValorantDataService : session Riot/Valorant
    ├── game_monitor.py       → GameMonitor : machine à états de surveillance
    ├── match_repository.py   → SQLite + migration automatique
    ├── file_manager.py       → RecordingManager : wait + rename + cleanup
    ├── obs_controller.py     → shim rétro-compatible
    ├── valorant_api.py       → shim rétro-compatible
    ├── monitor.py            → shim rétro-compatible
    ├── database.py           → shim rétro-compatible
    ├── config.py             → constantes
    ├── logger.py             → journalisation
    ├── main.py               → ancienne UI customtkinter (conservée)
    └── test_refonte.py       → 21 tests unitaires
```

**Communication Electron ↔ Python :** JSON-lines sur stdin/stdout.

---

## Prérequis

- **Windows 10/11** (cible).
- **OBS Studio 28+** (obs-websocket intégré). Les versions 27- (avec plugin) restent supportées via la couche d'abstraction.
- **Python 3.8+** installé et accessible dans le PATH (en mode dev). Pour un `.exe` autonome : PyInstaller.
- **Node.js 18+** et **npm** (pour développer / recompiler).
- **Valorant** et le **Riot Client** installés.

## Installation

```bash
git clone https://github.com/Paulo951-rgb/Valorant-Auto-Record-v1.git
cd Valorant-Auto-Record-v1
npm install
pip install -r requirements.txt
```

## Lancement en développement

```bash
npm start
```

L'application ouvre la fenêtre, lance le backend Python automatiquement et détecte OBS / Valorant.

## Création de l'exécutable Windows (.exe)

```bash
npm run build:win
```

Produit un installeur NSIS dans `dist/`.

Pour un `.exe` 100% autonome (sans Python sur la machine cible) :

```bash
cd python
pip install pyinstaller
pyinstaller --onefile --name backend backend.py
# copie dist/backend.exe dans python/ avant `npm run build:win`
```

## Configuration

Tous les paramètres sont éditables dans **Paramètres** et persistés dans `python/config_local.json`.

| Section | Paramètres |
|---|---|
| **Général** | Enregistrement automatique, lancement OBS automatique, démarrage avec Windows, réduction dans la zone de notification |
| **Enregistrements** | Dossier (auto-détecté depuis OBS), format, nettoyage auto, modèle de nom de fichier |
| **OBS** | Installation détectée, hôte, port, mot de passe WebSocket (test / reconnect / lancer) |
| **Avancé** *(masqué par défaut)* | Intervalle de sondage, niveau de logs |

## Fonctionnement

```
Premier lancement
  → détection OBS multi-chemin
  → connexion WebSocket (sinon lancement discret + attente)
  → si Valorant absent : attente
  → si partie détectée : start_record + attente confirmation
  → partie terminée : stop_record + finalisation fichier
  → insertion en base avec match_id unique
  → notification dans la page "Parties"
```

## Dépannage

| Symptôme | Solution |
|---|---|
| « OBS fermé » au démarrage | OBS sera lancé automatiquement. Si échec, vérifiez que OBS 28+ est installé. |
| « WebSocket indisponible » | Allez dans **Paramètres → OBS → Tester la connexion**. Reconnectez-vous. |
| « Backend déconnecté » | Cliquez sur **Redémarrer** dans l'Accueil. Vérifiez Python + `requirements.txt`. |
| Match non détecté | Le mode « agent select » peut être ignoré (c'est volontaire). Lancez la partie. |
| Vidéo non renommée | Le dossier OBS détecté doit être accessible en écriture. |

Diagnostics complets dans l'onglet **Diagnostics** (statut OBS, version, dossier, logs temps réel).

## Tests

```bash
cd python
python3 test_refonte.py
```

21 tests : OBS, Valorant, enregistrement, historique (migration, upsert, suppression), logique de monitor, protocole JSON-lines, gestion d'entrées invalides.

## Sécurité

- Communication Electron ↔ Python via `stdin/stdout` uniquement (aucun port exposé).
- L'API Riot utilisée est strictement locale (`127.0.0.1`) via le lockfile du client.
- Aucun mot de passe par défaut en clair : le mot de passe OBS est saisi par l'utilisateur (champ masqué) et stocké dans `config_local.json` (fichier local, non transmis).
- `extraResources` du build : seul `python/` et `requirements.txt` sont copiés, isolés du contenu utilisateur.

## Licence

MIT — voir le dépôt d'origine.

> Ce projet est fourni à titre éducatif. Valorant et Riot Games sont des marques de Riot Games. Ce projet n'est pas affilié à Riot Games.
