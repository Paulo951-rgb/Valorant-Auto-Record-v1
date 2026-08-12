# logger.py
import datetime

# Callback que l'interface graphique va enregistrer pour recevoir les logs
ui_callback = None

def log(message, level="INFO"):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    formatted_msg = f"[{now}] [{level}] {message}"

    # Affichage classique dans la console
    print(formatted_msg)

    # Envoi à l'interface graphique si elle est active
    if ui_callback:
        ui_callback(formatted_msg)

def info(message): log(message, "INFO")
def success(message): log(message, "SUCCESS")
def warning(message): log(message, "WARNING")
def debug(message): log(message, "DEBUG")
def error(message, exc=None):
    log(message, "ERROR")
    if exc is not None:
        log(f"           -> {exc}", "ERROR")