# main.py
import json
import os
import threading
import time
from datetime import datetime
import customtkinter as ctk
from tkinter import filedialog

import config
import database
import file_manager
import logger
from obs_controller import (
    test_obs_connection,
    start_record,
    stop_record,
    launch_obs,
    is_obs_running
)
from valorant_api import get_current_state, RiotClientNotRunning

CONFIG_FILE = "config_local.json"

class ModernRecorderApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Valorant Auto Recorder Pro")
        self.geometry("800x550")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Initialisations
        database.init_db()
        self.load_local_config()
        self.running = False
        self.monitor_thread = None

        # Redirection des logs vers l'interface
        logger.ui_callback = self.append_log_to_ui

        # Configuration de la grille principale
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Création des onglets
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")
        
        self.tab_dashboard = self.tabview.add("Tableau de bord")
        self.tab_history = self.tabview.add("Historique")
        self.tab_logs = self.tabview.add("Logs")
        self.tab_settings = self.tabview.add("Paramètres")

        self.setup_dashboard_tab()
        self.setup_history_tab()
        self.setup_logs_tab()
        self.setup_settings_tab()

        self.load_history()

    # --- CHARGEMENT CONFIGURATION LOCALE ---
    def load_local_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                self.local_config = json.load(f)
        else:
            self.local_config = {
                "obs_folder": os.path.expanduser("~/Videos"),
                "obs_exe_path": r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"
            }
        # Injecter dans le module config global pour obs_controller
        config.OBS_EXE_PATH = self.local_config["obs_exe_path"]

    def save_local_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.local_config, f)
        config.OBS_EXE_PATH = self.local_config["obs_exe_path"]

    # --- ONGLET 1 : TABLEAU DE BORD ---
    def setup_dashboard_tab(self):
        self.tab_dashboard.grid_columnconfigure(0, weight=1)

        self.lbl_status = ctk.CTkLabel(self.tab_dashboard, text="Statut : Inactif", font=("Arial", 20, "bold"), text_color="red")
        self.lbl_status.pack(pady=20)

        self.lbl_game_info = ctk.CTkLabel(self.tab_dashboard, text="En attente de détection du client Riot...", font=("Arial", 14))
        self.lbl_game_info.pack(pady=10)

        self.btn_toggle = ctk.CTkButton(self.tab_dashboard, text="Démarrer la Surveillance", command=self.toggle_monitoring, fg_color="green", hover_color="darkgreen")
        self.btn_toggle.pack(pady=20, ipadx=10, ipady=5)

    def toggle_monitoring(self):
        if not self.running:
            self.running = True
            self.lbl_status.configure(text="Statut : Surveillance active", text_color="green")
            self.btn_toggle.configure(text="Arrêter la Surveillance", fg_color="red", hover_color="darkred")
            self.monitor_thread = threading.Thread(target=self.monitoring_loop, daemon=True)
            self.monitor_thread.start()
        else:
            self.running = False
            self.lbl_status.configure(text="Statut : Inactif", text_color="red")
            self.btn_toggle.configure(text="Démarrer la Surveillance", fg_color="green", hover_color="darkgreen")

    # --- ONGLET 2 : HISTORIQUE ---
    def setup_history_tab(self):
        self.tab_history.grid_columnconfigure(0, weight=1)
        self.tab_history.grid_rowconfigure(0, weight=1)

        self.history_textbox = ctk.CTkTextbox(self.tab_history, width=700, height=350)
        self.history_textbox.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

    def load_history(self):
        self.history_textbox.configure(state="normal")
        self.history_textbox.delete("1.0", ctk.END)
        matches = database.get_all_matches()
        if not matches:
            self.history_textbox.insert(ctk.END, "Aucun match enregistré pour le moment.\n")
        else:
            for row in matches:
                # row: (id, date, map, agent, score, result, path)
                entry = f"[{row[1]}] Map : {row[2]} | Agent : {row[3]} | Score : {row[4]} ({row[5]})\n Fichier : {row[6]}\n"
                entry += "-" * 80 + "\n"
                self.history_textbox.insert(ctk.END, entry)
        self.history_textbox.configure(state="disabled")

    # --- ONGLET 3 : LOGS ---
    def setup_logs_tab(self):
        self.tab_logs.grid_columnconfigure(0, weight=1)
        self.tab_logs.grid_rowconfigure(0, weight=1)

        self.log_textbox = ctk.CTkTextbox(self.tab_logs, width=700, height=350, font=("Courier New", 12))
        self.log_textbox.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.log_textbox.configure(state="disabled")

    def append_log_to_ui(self, message):
        """Méthode thread-safe pour insérer les logs."""
        def action():
            self.log_textbox.configure(state="normal")
            self.log_textbox.insert(ctk.END, message + "\n")
            self.log_textbox.see(ctk.END)
            self.log_textbox.configure(state="disabled")
        self.after(0, action)

    # --- ONGLET 4 : PARAMÈTRES ---
    def setup_settings_tab(self):
        self.tab_settings.grid_columnconfigure(1, weight=1)

        # Chemin enregistrements OBS
        ctk.CTkLabel(self.tab_settings, text="Dossier Enregistrements OBS :").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.entry_obs_folder = ctk.CTkEntry(self.tab_settings, width=400)
        self.entry_obs_folder.insert(0, self.local_config["obs_folder"])
        self.entry_obs_folder.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        ctk.CTkButton(self.tab_settings, text="Parcourir...", command=self.browse_obs_folder).grid(row=0, column=2, padx=10, pady=10)

        # Chemin exécutable OBS
        ctk.CTkLabel(self.tab_settings, text="Exécutable OBS Studio (obs64.exe) :").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        self.entry_obs_exe = ctk.CTkEntry(self.tab_settings, width=400)
        self.entry_obs_exe.insert(0, self.local_config["obs_exe_path"])
        self.entry_obs_exe.grid(row=1, column=1, padx=10, pady=10, sticky="ew")
        ctk.CTkButton(self.tab_settings, text="Parcourir...", command=self.browse_obs_exe).grid(row=1, column=2, padx=10, pady=10)

        # Sauvegarde
        ctk.CTkButton(self.tab_settings, text="Enregistrer les paramètres", fg_color="blue", command=self.save_settings).grid(row=2, column=1, pady=20, sticky="e")

    def browse_obs_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.entry_obs_folder.delete(0, ctk.END)
            self.entry_obs_folder.insert(0, folder)

    def browse_obs_exe(self):
        file = filedialog.askopenfilename(filetypes=[("Applications", "*.exe")])
        if file:
            self.entry_obs_exe.delete(0, ctk.END)
            self.entry_obs_exe.insert(0, file)

    def save_settings(self):
        self.local_config["obs_folder"] = self.entry_obs_folder.get()
        self.local_config["obs_exe_path"] = self.entry_obs_exe.get()
        self.save_local_config()
        logger.success("Paramètres enregistrés localement avec succès.")

    # --- LOGIQUE PRINCIPALE DE SURVEILLANCE ---
    def monitoring_loop(self):
        previous_state = None
        current_map = "Inconnu"
        current_agent = "Inconnu"

        while self.running:
            try:
                state_data = get_current_state(debug=config.DEBUG)
                state = state_data["state"]
                current_map = state_data["map"]
                current_agent = state_data["agent"]
                
                # Mise à jour graphique des infos du client
                info_text = f"Carte : {current_map}  |  Agent : {current_agent}  |  Statut : {state}"
                self.after(0, lambda: self.lbl_game_info.configure(text=info_text))

            except RiotClientNotRunning:
                previous_state = None
                self.after(0, lambda: self.lbl_game_info.configure(text="Riot Client non détecté (Valorant éteint)"))
                time.sleep(config.POLL_INTERVAL)
                continue
            except Exception as e:
                logger.error(f"Erreur API Riot : {e}")
                time.sleep(config.POLL_INTERVAL)
                continue

            # Passage vers INGAME => Lancement OBS et Enregistrement
            if state == "INGAME" and previous_state != "INGAME":
                logger.info("Détection du début de partie.")
                
                # Vérifier et lancer OBS automatiquement
                if not is_obs_running():
                    launch_obs()
                    
                # Tentative de connexion / démarrage de l'enregistrement
                if test_obs_connection():
                    start_record()
                else:
                    logger.error("OBS n'a pas pu être rejoint pour lancer l'enregistrement.")

            # Sortie de INGAME => Arrêt et Renommage
            elif previous_state == "INGAME" and state != "INGAME":
                logger.info("Détection de la fin du match.")
                stop_record()

                # Traitement de fichier (récupération du dossier configuré par l'utilisateur)
                obs_folder = self.local_config["obs_folder"]
                score_final = state_data.get("score", "0-0")
                
                # Détermination du vainqueur approximatif (si notre score > ennemi)
                try:
                    scores = [int(x) for x in score_final.split('-')]
                    result_str = "Victoire" if scores[0] > scores[1] else "Defaite"
                except Exception:
                    result_str = "FinMatch"

                new_path = file_manager.rename_recording(obs_folder, current_map, current_agent, score_final, result_str)
                
                if new_path:
                    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    database.add_match(date_now, current_map, current_agent, score_final, result_str, new_path)
                    
                    # Nettoyage automatique (limite de 50 Go)
                    file_manager.clean_old_recordings(obs_folder, max_size_gb=50)
                    
                    # Rafraîchir l'historique
                    self.after(0, self.load_history)

            previous_state = state
            time.sleep(config.POLL_INTERVAL)

if __name__ == "__main__":
    app = ModernRecorderApp()
    app.mainloop()