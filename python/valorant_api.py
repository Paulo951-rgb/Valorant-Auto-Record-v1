# valorant_api.py
import base64
import json
import os
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LOCKFILE_PATH = os.path.expandvars(r"%LOCALAPPDATA%\Riot Games\Riot Client\Config\lockfile")

class RiotClientNotRunning(Exception):
    pass

MAP_MAPPING = {
    "/Game/Maps/Duality/Duality": "Bind",
    "/Game/Maps/Triad/Triad": "Haven",
    "/Game/Maps/Bonsai/Bonsai": "Split",
    "/Game/Maps/Ascent/Ascent": "Ascent",
    "/Game/Maps/Port/Port": "Breeze",
    "/Game/Maps/Canyon/Canyon": "Fracture",
    "/Game/Maps/Pitt/Pitt": "Pearl",
    "/Game/Maps/Jam/Jam": "Lotus",
    "/Game/Maps/Sunset/Sunset": "Sunset",
    "/Game/Maps/Burrow/Burrow": "Abyss",
}

AGENT_MAPPING = {
    "Clay": "Raze", "Vampire": "Reyna", "Wraith": "Omen", "Grizzly": "Breach",
    "Hunter": "Sova", "Rift": "Astra", "Phoenix": "Phoenix", "Sarge": "Brimstone",
    "Ninja": "Jett", "Gumshoe": "Cypher", "BountyHunter": "Fade", "Aggro": "Gekko",
    "Nouveau": "Iso", "Mage": "Harbor", "Sprinter": "Neon", "Deadeye": "Chamber",
    "Seis": "Sage", "Thief": "Yoru", "Cable": "Deadlock", "Chronovoid": "Clove",
    "Sentry": "Killjoy", "Thorn": "Vyse", "Teatime": "Teatime/Agent"
}

def read_lockfile():
    if not os.path.exists(LOCKFILE_PATH):
        raise RiotClientNotRunning("Riot Client fermé")
    with open(LOCKFILE_PATH, "r", encoding="utf-8") as f:
        parts = f.read().strip().split(":")
    if len(parts) != 5:
        raise RiotClientNotRunning("lockfile invalide")
    return {"port": parts[2], "password": parts[3]}

def get_own_puuid(port, password):
    url = f"https://127.0.0.1:{port}/chat/v1/session"
    r = requests.get(url, headers={"Authorization": f"Basic {base64.b64encode(f'riot:{password}'.encode()).decode()}"}, verify=False, timeout=3)
    r.raise_for_status()
    return r.json()["puuid"]

def get_current_state(debug=False):
    """
    Retourne un dictionnaire contenant les détails de la session.
    """
    try:
        creds = read_lockfile()
        puuid = get_own_puuid(creds["port"], creds["password"])
    except Exception:
        raise RiotClientNotRunning()

    url = f"https://127.0.0.1:{creds['port']}/chat/v4/presences"
    r = requests.get(url, headers={"Authorization": f"Basic {base64.b64encode(f'riot:{creds['password']}'.encode()).decode()}"}, verify=False, timeout=3)
    r.raise_for_status()

    presences = r.json().get("presences", [])
    for p in presences:
        if p.get("puuid") == puuid and p.get("product") == "valorant":
            private_raw = p.get("private")
            if not private_raw:
                return {"state": "MENUS", "map": "Inconnu", "agent": "Inconnu"}
            
            try:
                info = json.loads(base64.b64decode(private_raw))
                match_data = info.get("matchPresenceData") or {}
                state = match_data.get("sessionLoopState") or "MENUS"
                
                # Récupération de la carte et de l'agent
                raw_map = info.get("matchMap")
                map_name = MAP_MAPPING.get(raw_map, "Inconnu")
                
                # Extraction du nom de l'agent depuis le chemin d'accès
                agent_path = info.get("characterCharacter")
                agent_name = "Inconnu"
                if agent_path:
                    parts = agent_path.split('/')
                    if len(parts) > 3:
                        agent_name = AGENT_MAPPING.get(parts[3], parts[3])

                # Extraction du score
                score_ours = info.get("partyOwnerMatchScoreAllyTeam") or 0
                score_theirs = info.get("partyOwnerMatchScoreEnemyTeam") or 0
                score_str = f"{score_ours}-{score_theirs}"

                return {
                    "state": state,
                    "map": map_name,
                    "agent": agent_name,
                    "score": score_str
                }
            except Exception as e:
                if debug:
                    print(f"Erreur décodage présence : {e}")
                return {"state": "MENUS", "map": "Inconnu", "agent": "Inconnu"}

    return {"state": "MENUS", "map": "Inconnu", "agent": "Inconnu"}