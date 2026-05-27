import json
import os
from app_paths import data_file


CONFIG_FILE = str(data_file("gologin_settings.json", {
    "api_key": "",
    "use_gologin_cloud": False,
    "gologin_folder_name": "",
}))

DEFAULT_SETTINGS = {
    "api_key": "",
    "use_gologin_cloud": False,
    "gologin_folder_name": "",
}


def load_gologin_settings():
    settings = DEFAULT_SETTINGS.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                settings.update({k: v for k, v in data.items() if k in settings})
        except Exception:
            pass

    env_key = os.environ.get("GOLOGIN_API_KEY") or os.environ.get("GOLOGIN_TOKEN")
    if env_key:
        settings["api_key"] = env_key.strip()
    return settings


def save_gologin_settings(api_key="", use_gologin_cloud=False, gologin_folder_name=""):
    settings = {
        "api_key": (api_key or "").strip(),
        "use_gologin_cloud": bool(use_gologin_cloud),
        "gologin_folder_name": (gologin_folder_name or "").strip(),
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)
    return settings


def get_gologin_api_key():
    return load_gologin_settings().get("api_key", "").strip()


def mask_secret(value):
    value = (value or "").strip()
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
