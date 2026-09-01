# logger.py
import datetime
import threading

# Callback que l'interface graphique va enregistrer pour recevoir les logs
ui_callback = None
_lock = threading.Lock()

LEVELS = ("DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR")


def log(message, level="INFO"):
    if level not in LEVELS:
        level = "INFO"
    now = datetime.datetime.now().strftime("%H:%M:%S")
    formatted_msg = f"[{now}] [{level}] {message}"
    try:
        print(formatted_msg)
    except Exception:
        pass
    with _lock:
        cb = ui_callback
    if cb:
        try:
            cb(formatted_msg)
        except Exception:
            pass


def info(message):    log(message, "INFO")
def success(message): log(message, "SUCCESS")
def warning(message): log(message, "WARNING")
def debug(message):   log(message, "DEBUG")
def error(message, exc=None):
    log(message, "ERROR")
    if exc is not None:
        log(f"           -> {exc}", "ERROR")
