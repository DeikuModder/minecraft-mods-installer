import json
import os

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "InstaladorMods")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULT = {
    "tunnel_name": "instalador-mods",
    "hostname": "",
    "last_origin_url": "",
    "folder_a": "",
    "folder_b": "",
    "role": "anfitrion",
    "update_repo": "",
}


def load():
    cfg = dict(DEFAULT)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save(datos):
    cfg = load()
    cfg.update(datos)
    os.makedirs(APP_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)