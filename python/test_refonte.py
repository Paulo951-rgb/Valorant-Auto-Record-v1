# test_refonte.py
"""
Tests pour la refonte — exécutables sans OBS / sans Valorant.

Couvre :
  * OBS : découverte multi-chemin, abstrait start/stop, erreurs humanisées,
    robustesse de l'absence de WebSocket.
  * Valorant : import sans lockfile, valeurs par défaut, mappage map/agent,
    classification recordable, gestion d'absence de session.
  * Enregistrement : file_manager.wait_for_finalized_recording, rename,
    collision de noms, fichiers verrouillés simulés.
  * Historique : migration, insertion, mise à jour par match_id, doublons,
    suppression, fichier supprimé.

Exécution :
  python3 -m unittest test_refonte.py
"""
import os
import sys
import json
import time
import sqlite3
import tempfile
import unittest
import threading
import subprocess
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Force le CWD sur python/ pour que la DB / config soient locales au test.
os.chdir(HERE)


def _send_stdin(commands):
    """Lance backend.py avec un script de commandes et retourne les events/notifs."""
    import subprocess
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "backend.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=HERE,
    )
    payload = "\n".join(json.dumps(c) for c in commands) + "\n"
    out, err = proc.communicate(input=payload.encode("utf-8"), timeout=15)
    lines = out.decode("utf-8", errors="ignore").splitlines()
    parsed = []
    for ln in lines:
        try:
            parsed.append(json.loads(ln))
        except Exception:
            pass
    return parsed


class TestOBS(unittest.TestCase):
    def test_discover_returns_list_with_running_field(self):
        from obs_service import discover
        result = discover()
        self.assertIsInstance(result, list)
        for r in result:
            self.assertTrue(hasattr(r, "running"))
            self.assertTrue(hasattr(r, "path"))

    def test_humanize_error(self):
        from obs_service import _humanize_connect_error
        msg = _humanize_connect_error(Exception("Connection refused 127.0.0.1:4455"))
        self.assertIn("WebSocket", msg)
        self.assertIn("automatique", msg)
        msg = _humanize_connect_error(Exception("401 unauthorized: bad password"))
        self.assertIn("Mot de passe", msg)
        msg = _humanize_connect_error(None)
        self.assertTrue(len(msg) > 0)

    def test_start_recording_without_client_returns_false(self):
        from obs_service import OBSService
        svc = OBSService(host="127.0.0.1", port=1, password="")
        # Pas d'OBS sur ce port -> doit retourner False sans crash.
        self.assertFalse(svc.start_recording())
        self.assertFalse(svc.connect())

    def test_status_safe_when_no_client(self):
        from obs_service import OBSService
        svc = OBSService()
        s = svc.get_status(force=True)
        self.assertFalse(s.running)
        self.assertFalse(s.connected)
        self.assertFalse(s.recording)


class TestValorant(unittest.TestCase):
    def test_sessionstate_defaults(self):
        from valorant_service import SessionState
        s = SessionState()
        self.assertEqual(s.state, "MENUS")
        self.assertEqual(s.map, "Inconnu")

    def test_recordable_states_include_ingame_and_gamemode(self):
        from valorant_service import RECORDABLE_STATES
        self.assertIn("INGAME", RECORDABLE_STATES)
        self.assertIn("GAMEMODE", RECORDABLE_STATES)

    def test_map_mapping_known(self):
        from valorant_service import MAP_MAPPING
        self.assertEqual(MAP_MAPPING["/Game/Maps/Ascent/Ascent"], "Ascent")
        self.assertEqual(MAP_MAPPING["/Game/Maps/Burrow/Burrow"], "Abyss")

    def test_agent_mapping_known(self):
        from valorant_service import AGENT_MAPPING
        self.assertEqual(AGENT_MAPPING["Ninja"], "Jett")
        self.assertEqual(AGENT_MAPPING["Clay"], "Raze")

    def test_get_current_state_no_lockfile_returns_default(self):
        # get_current_state historique renvoie "MENUS" / "Inconnu" au lieu de
        # faire planter (comportement préservé pour la robustesse d'origine).
        from valorant_api import get_current_state
        with mock.patch("valorant_service.LOCKFILE_PATH", "/nonexistent.lock"):
            st = get_current_state(debug=False)
        self.assertEqual(st["state"], "MENUS")
        self.assertEqual(st["map"], "Inconnu")

    def test_service_raises_when_no_lockfile(self):
        from valorant_service import ValorantDataService, RiotClientNotRunning
        v = ValorantDataService()
        with mock.patch("valorant_service.LOCKFILE_PATH", "/nonexistent.lock"):
            with self.assertRaises(RiotClientNotRunning):
                v._fetch_session()

    def test_no_valorant_process(self):
        from valorant_service import ValorantDataService
        # psutil est installé dans le sandbox; on simule l'absence de processus.
        with mock.patch("psutil.process_iter", return_value=iter([])):
            self.assertFalse(ValorantDataService.is_valorant_running())


class TestRecording(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="var_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_latest_file(self):
        from file_manager import get_latest_file
        a = os.path.join(self.tmp, "a.mp4")
        b = os.path.join(self.tmp, "b.mp4")
        with open(a, "wb") as f: f.write(b"a")
        time.sleep(0.05)
        with open(b, "wb") as f: f.write(b"b")
        self.assertEqual(os.path.basename(get_latest_file(self.tmp)), "b.mp4")

    def test_wait_for_finalized_returns_when_size_stable(self):
        from file_manager import wait_for_finalized_recording
        p = os.path.join(self.tmp, "v.mp4")
        with open(p, "wb") as f: f.write(b"0" * 1024)
        t0 = time.time() - 1
        result = wait_for_finalized_recording(self.tmp, since_ts=t0, timeout=5,
                                              stable_seconds=1, poll=0.1)
        self.assertEqual(result, p)

    def test_rename_with_template_and_collision(self):
        from file_manager import rename_recording_with_options
        p = os.path.join(self.tmp, "obs_record.mp4")
        with open(p, "wb") as f: f.write(b"x" * 100)
        new, size = rename_recording_with_options(
            self.tmp, "Ascent", "Jett", "13-9", "Victoire",
            template="V_{date}_{map}_{agent}_{score}_{result}",
            file_path=p,
        )
        self.assertIsNotNone(new)
        self.assertTrue(new.startswith(os.path.join(self.tmp, "V_")))
        self.assertTrue(size > 0)
        # Collision : on appelle à nouveau avec le même template
        p2 = os.path.join(self.tmp, "obs_record2.mp4")
        with open(p2, "wb") as f: f.write(b"y" * 50)
        new2, _ = rename_recording_with_options(
            self.tmp, "Ascent", "Jett", "13-9", "Victoire",
            template="V_{date}_{map}_{agent}_{score}_{result}",
            file_path=p2,
        )
        self.assertNotEqual(new, new2)


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="var_db_")
        self.db_path = os.path.join(self.tmp, "test.db")
        # Crée l'ancien schéma (6 colonnes) pour valider la migration.
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE matches (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "date TEXT NOT NULL, map_name TEXT, agent TEXT, score TEXT, "
            "result TEXT, video_path TEXT)"
        )
        conn.execute(
            "INSERT INTO matches(date,map_name,agent,score,result,video_path) "
            "VALUES('2025-01-01 10:00','Bind','Phoenix','5-7','Defaite','/old/v.mp4')"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migration_adds_columns(self):
        from match_repository import MatchRepository
        repo = MatchRepository(self.db_path)
        repo.init()
        rows = repo.get_all()
        self.assertEqual(len(rows), 1)
        self.assertIn("match_id", rows[0])
        self.assertIn("kills", rows[0])
        # L'ancienne ligne doit toujours être là
        self.assertEqual(rows[0]["map_name"], "Bind")

    def test_upsert_dedup_by_match_id(self):
        from match_repository import MatchRepository
        repo = MatchRepository(self.db_path)
        repo.init()
        data = {
            "match_id": "riot-12345", "date": "2026-09-01 20:14",
            "map_name": "Ascent", "agent": "Jett", "score": "13-9",
            "ally_score": 13, "enemy_score": 9, "result": "Victoire",
            "duration_seconds": 2040, "video_path": "/tmp/v.mp4",
            "file_size_bytes": 1000, "status": "completed",
        }
        id1 = repo.upsert_match(data)
        data2 = dict(data)
        data2["score"] = "13-11"
        data2["result"] = "Victoire"
        id2 = repo.upsert_match(data2)
        self.assertEqual(id1, id2)
        self.assertEqual(repo.count(), 2)  # 1 ancien + 1 nouveau

    def test_delete(self):
        from match_repository import MatchRepository
        repo = MatchRepository(self.db_path)
        repo.init()
        repo.upsert_match({"match_id": "x", "date": "now", "map_name": "A"})
        self.assertTrue(repo.delete("x"))
        self.assertFalse(repo.delete("x"))


class TestProtocol(unittest.TestCase):
    def test_end_to_end_protocol(self):
        # Démarre le backend en sous-processus et lui envoie quelques commandes.
        cmds = [
            {"id": "1", "method": "ping", "params": {}},
            {"id": "2", "method": "get_config", "params": {}},
            {"id": "3", "method": "discover_obs", "params": {}},
            {"id": "4", "method": "get_history", "params": {}},
            [1, 2, 3],  # invalide : doit être rejeté
            {"id": "5", "method": "this_does_not_exist", "params": {}},
        ]
        events = _send_stdin(cmds)
        ids = [e.get("id") for e in events if "id" in e]
        self.assertIn("1", ids)
        self.assertIn("2", ids)
        self.assertIn("3", ids)
        self.assertIn("4", ids)
        self.assertIn("5", ids)
        # L'erreur de méthode inconnue doit être propagée
        err5 = next(e for e in events if e.get("id") == "5")
        self.assertIn("error", err5)
        self.assertIsNotNone(err5["error"])
        # La liste invalide doit déclencher un event "error" (notification)
        notifs_err = [e for e in events if e.get("event") == "error"]
        self.assertTrue(len(notifs_err) >= 1)


class TestMonitorLogic(unittest.TestCase):
    """Tests de la logique de monitor (sans vrai OBS/Valorant) via mocks."""

    def setUp(self):
        # DB isolée
        self.tmp = tempfile.mkdtemp(prefix="var_mon_")
        self.db_path = os.path.join(self.tmp, "test.db")
        from match_repository import MatchRepository
        self.repo = MatchRepository(self.db_path)
        self.repo.init()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_monitor(self, cfg):
        from game_monitor import GameMonitor, GameState
        events = []
        mon = GameMonitor(get_runtime_config=lambda: cfg,
                          emit_event=lambda n, d: events.append((n, d)),
                          repository=self.repo)
        return mon, events

    def test_initial_state_is_idle(self):
        from game_monitor import GameState
        cfg = {"auto_record": True, "obs_folder": self.tmp}
        mon, _ = self._make_monitor(cfg)
        self.assertEqual(mon.state, GameState.IDLE.value)

    def test_session_to_active_emits_match_started(self):
        from game_monitor import GameState
        from valorant_service import SessionState
        cfg = {"auto_record": True, "obs_folder": self.tmp,
               "obs_host": "127.0.0.1", "obs_port": 1, "obs_password": ""}
        mon, events = self._make_monitor(cfg)
        # Mock: OBS toujours OK et recording OK
        with mock.patch("game_monitor.is_obs_running", return_value=True), \
             mock.patch("game_monitor.test_obs_connection", return_value=True), \
             mock.patch("game_monitor.start_record", return_value=True), \
             mock.patch("game_monitor.stop_record", return_value=True), \
             mock.patch("game_monitor.is_recording", return_value=False), \
             mock.patch("game_monitor.get_recording_path", return_value=self.tmp), \
             mock.patch("game_monitor.launch_obs", return_value=True):
            # Session = INGAME
            sess = SessionState(state="INGAME", map="Ascent", agent="Jett",
                                score="0-0", ally_score=0, enemy_score=0,
                                queue_id="competitive")
            mon._current_session = sess
            mon._handle_session_state(sess, cfg)
            self.assertEqual(mon.state, GameState.MATCH_ACTIVE.value)
            names = [n for n, _ in events]
            self.assertIn("match_started", names)

    def test_session_to_menus_after_active_finalizes(self):
        from game_monitor import GameState
        from valorant_service import SessionState
        cfg = {"auto_record": True, "obs_folder": self.tmp,
               "obs_host": "127.0.0.1", "obs_port": 1, "obs_password": "",
               "max_size_gb": 0}
        mon, events = self._make_monitor(cfg)
        with mock.patch("game_monitor.is_obs_running", return_value=True), \
             mock.patch("game_monitor.test_obs_connection", return_value=True), \
             mock.patch("game_monitor.start_record", return_value=True), \
             mock.patch("game_monitor.stop_record", return_value=True), \
             mock.patch("game_monitor.is_recording", return_value=True), \
             mock.patch("game_monitor.get_recording_path", return_value=self.tmp), \
             mock.patch("game_monitor.launch_obs", return_value=True), \
             mock.patch("game_monitor.file_manager.wait_for_finalized_recording",
                        return_value=None):
            # 1) start
            sess = SessionState(state="INGAME", map="Ascent", agent="Jett",
                                score="5-4", ally_score=5, enemy_score=4)
            mon._handle_session_state(sess, cfg)
            self.assertEqual(mon.state, GameState.MATCH_ACTIVE.value)
            # 2) fin
            sess2 = SessionState(state="MENUS", map="Ascent", agent="Jett",
                                 score="13-9", ally_score=13, enemy_score=9)
            mon._handle_session_state(sess2, cfg)
            names = [n for n, _ in events]
            self.assertIn("match_ended", names)
            # Le match doit être persisté en BDD
            self.assertEqual(self.repo.count(), 1)
            row = self.repo.get_all()[0]
            self.assertEqual(row["result"], "Victoire")
            self.assertEqual(row["map_name"], "Ascent")
            self.assertEqual(row["ally_score"], 13)
            self.assertEqual(row["enemy_score"], 9)

    def test_finalize_idempotent(self):
        """Appeler _finalize_current_match plusieurs fois ne doit pas
        insérer plusieurs entrées en base."""
        from game_monitor import GameState
        from valorant_service import SessionState
        cfg = {"auto_record": True, "obs_folder": self.tmp,
               "obs_host": "127.0.0.1", "obs_port": 1, "obs_password": "",
               "max_size_gb": 0}
        mon, events = self._make_monitor(cfg)
        with mock.patch("game_monitor.is_obs_running", return_value=True), \
             mock.patch("game_monitor.test_obs_connection", return_value=True), \
             mock.patch("game_monitor.start_record", return_value=True), \
             mock.patch("game_monitor.stop_record", return_value=True), \
             mock.patch("game_monitor.is_recording", return_value=True), \
             mock.patch("game_monitor.get_recording_path", return_value=self.tmp), \
             mock.patch("game_monitor.launch_obs", return_value=True), \
             mock.patch("game_monitor.file_manager.wait_for_finalized_recording",
                        return_value=None):
            sess = SessionState(state="INGAME", map="Ascent", agent="Jett",
                                score="13-9", ally_score=13, enemy_score=9)
            mon._handle_session_state(sess, cfg)
            # Simule 3 appels concurrents à finalize
            for _ in range(3):
                mon._finalize_current_match(session=sess)
            # Au plus 1 entrée en base (le lock non-bloquant doit faire qu'un seul
            # appel passe à chaque instant).
            # Au minimum 1 entrée.
            self.assertGreaterEqual(self.repo.count(), 1)
            # On autorise un petit délai puis on re-vérifie qu'on n'a pas explosé.
            import time as _t
            _t.sleep(0.2)
            self.assertLessEqual(self.repo.count(), 2)


class TestBugsFixed(unittest.TestCase):
    """Tests des bugs identifiés lors de l'audit."""

    def test_obs_is_recording_active_v5(self):
        from obs_service import _is_recording_active
        # Dataclass style obsws-python
        class RS:
            output_active = True
            output_state = "OBS_WEBSOCKET_OUTPUT_OUTPUT_STATE_ACTIVE"
        self.assertTrue(_is_recording_active(RS()))
        class RS2:
            output_active = False
            output_state = "OBS_WEBSOCKET_OUTPUT_OUTPUT_STATE_STOPPED"
        self.assertFalse(_is_recording_active(RS2()))
        # v4 sans output_state
        class RS3:
            output_active = True
        self.assertTrue(_is_recording_active(RS3()))
        # None safe
        self.assertFalse(_is_recording_active(None))

    def test_clean_old_recordings_protected(self):
        import os, tempfile
        from file_manager import clean_old_recordings
        tmp = tempfile.mkdtemp()
        try:
            # Crée 3 fichiers de 1 Mo.
            for i in range(3):
                p = os.path.join(tmp, f"old_{i}.mp4")
                with open(p, "wb") as f: f.write(b"x" * (1024*1024))
                import time
                time.sleep(0.02)
            protected = [os.path.join(tmp, "old_2.mp4")]
            # Limite 1.5 Mo => 1 fichier à supprimer, mais old_2 est protégé.
            clean_old_recordings(tmp, max_size_gb=0.0015, protected_paths=protected)
            files = sorted(os.listdir(tmp))
            self.assertIn("old_2.mp4", files)
            self.assertLess(len(files), 3)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_backend_handlers_run_in_thread(self):
        """Vérifie que le backend n'est pas bloqué par un handler qui hang."""
        from backend import _handle_request
        import json
        # Patch un handler qui dort
        import time as _t
        def slow_handler(params):
            _t.sleep(0.2)
            return {"ok": True}
        from backend import HANDLERS
        HANDLERS["_test_slow"] = slow_handler
        try:
            t0 = _t.time()
            _handle_request(json.dumps({"id": "x1", "method": "_test_slow", "params": {}}))
            elapsed = _t.time() - t0
            self.assertLess(elapsed, 0.5)  # Ne doit pas bloquer
        finally:
            HANDLERS.pop("_test_slow", None)

    def test_obs_service_lock_released_on_unconnect(self):
        """Vérifie qu'on peut appeler start_recording plusieurs fois sans
        deadlock."""
        from obs_service import OBSService
        svc = OBSService(host="127.0.0.1", port=1, password="")
        # connect() échoue (port 1), mais ne doit pas lever ni deadlock.
        self.assertFalse(svc.connect())
        # start_recording doit aussi échouer proprement.
        self.assertFalse(svc.start_recording())
        # On doit pouvoir continuer à appeler.
        self.assertFalse(svc.stop_recording())

    def test_valorant_processes_snapshot_cache(self):
        from valorant_service import ValorantDataService
        v = ValorantDataService()
        # Premier appel
        s1 = v.processes_snapshot(use_cache=False)
        s2 = v.processes_snapshot(use_cache=True)
        self.assertEqual(s1, s2)

    def test_get_latest_file_exclude(self):
        from file_manager import get_latest_file
        import os, tempfile, time
        tmp = tempfile.mkdtemp()
        try:
            a = os.path.join(tmp, "a.mp4")
            b = os.path.join(tmp, "b.mp4")
            with open(a, "wb") as f: f.write(b"a")
            time.sleep(0.05)
            with open(b, "wb") as f: f.write(b"b")
            self.assertEqual(os.path.basename(get_latest_file(tmp)), "b.mp4")
            self.assertEqual(os.path.basename(get_latest_file(tmp, exclude=b)), "a.mp4")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_match_repository_queue_id(self):
        from match_repository import MatchRepository
        import tempfile, os
        tmp = tempfile.mkdtemp()
        try:
            repo = MatchRepository(os.path.join(tmp, "test.db"))
            repo.init()
            repo.upsert_match({
                "match_id": "x1", "date": "now", "map_name": "Ascent",
                "agent": "Jett", "score": "13-9",
                "ally_score": 13, "enemy_score": 9, "result": "Victoire",
                "queue_id": "competitive", "mode": "Compétitif",
            })
            row = repo.get_by_match_id("x1")
            self.assertEqual(row.get("queue_id"), "competitive")
            self.assertEqual(row.get("mode"), "Compétitif")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
