# Valorant Auto Record — notes projet

## Architecture
Application Electron + backend Python. Le moteur Python original (config, obs_controller,
valorant_api, database, file_manager, logger) est CONSERVÉ dans `python/`. Electron communique
avec le backend via un protocole JSON-lines sur stdin/stdout (backend_bridge.js → backend.py).

## Fichiers clés
- `python/backend.py` — point d'entrée backend (JSON-lines). Redirige stdout vers stderr, écrit les
  réponses JSON sur le flux stdout d'origine pour ne pas corrompre le protocole.
- `python/monitor.py` — classe `Monitor` extraite de `main.py` (boucle de surveillance préservée).
- `electron/main.js` / `preload.js` / `backend_bridge.js` — processus principal Electron.
- `renderer/` — UI (index.html, styles.css, renderer.js). Thème tactique Valorant (coral #ff4655).
- `python/main.py` — ancienne UI customtkinter, conservée comme alternative autonome.

## Commandes
- Dev: `npm start` / `npm run dev` (Electron lance automatiquement le backend Python).
- Lint: `npm run lint` (vérifie la syntaxe de tous les JS).
- Build Windows: `npm run build:win` (electron-builder NSIS x64).
- Dépendances Python: `pip install -r requirements.txt` (obsws-python, requests, colorama, psutil).

## Tests réalisés (sandbox Linux, sans OBS/Valorant)
- Protocole backend : ping/get_config/save_config/get_status/get_history/monitoring/valo_state
  testés via stdin/stdout. Tous OK. Méthode inconnue gérée proprement.
- Persistance config : save_config → get_config après redémarrage backend OK.
- Logique monitor.py : tests unitaires (début/fin de partie, victoire/défaite, auto_record off)
  TOUS PASSÉS — la logique préservée d'origine se déclenche correctement.
- App Electron sous xvfb : UI se charge (4 onglets, 5 cartes, 14 boutons), backend Python lancé,
  événements ready/status/log reçus par le renderer, AUCUNE erreur JS.

## Points d'attention
- La communication se fait UNIQUEMENT via stdin/stdout (aucun port exposé).
- `config_local.json` (dans python/) est la source unique de vérité pour la config.
- Pour un .exe autonome : PyInstaller sur backend.py → backend.exe (détecté automatiquement).
- Sur Linux/dev, le sandbox Chromium nécessite --no-sandbox (problème SUID, pas de code).
- backend.py fait `os.chdir(_HERE)` pour que DB et config_local.json soient à côté des scripts.
