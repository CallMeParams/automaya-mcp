"""Settings and API keys, stored in a JSON file under the Maya app dir.

Resolution order for a key like ``TRIPO_API_KEY``: environment variable,
then the prefs file. Keys typed into the console are saved to the prefs
file with 0600 permissions. Nothing here is ever sent to the MCP server
except a boolean "configured" flag; the server reads the same env vars
itself for provider calls, and the plugin only needs keys for downloads
that happen inside Maya.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

PROVIDER_KEYS = [
    "TRIPO_API_KEY",
    "MESHY_API_KEY",
    "RODIN_API_KEY",
    "FAL_KEY",
    "HUNYUAN_SECRET_ID",
    "HUNYUAN_SECRET_KEY",
    "HUNYUAN_LOCAL_URL",
    "HIGGSFIELD_API_KEY",
    "HIGGSFIELD_API_SECRET",
    "SKETCHFAB_API_TOKEN",
    "POLYPIZZA_API_KEY",
]

DEFAULTS: Dict[str, Any] = {
    "port": 9877,
    "event_port": 9878,
    "auto_start": True,
    "auto_events": True,
    "safe_mode": False,
    "integrations": {
        "polyhaven": True,
        "sketchfab": False,
        "polypizza": False,
        "tripo": False,
        "meshy": False,
        "rodin": False,
        "hunyuan": False,
        "higgsfield": False,
    },
    "keys": {},
}


def prefs_path() -> str:
    base = os.environ.get("MAYA_APP_DIR")
    if not base:
        home = os.path.expanduser("~")
        base = os.path.join(home, "Documents", "maya") if os.name == "nt" else os.path.join(home, "maya")
    folder = os.path.join(base, "automaya")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "prefs.json")


def load() -> Dict[str, Any]:
    data = json.loads(json.dumps(DEFAULTS))
    try:
        with open(prefs_path(), encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return data
    for k, v in stored.items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k].update(v)
        else:
            data[k] = v
    return data


def save(data: Dict[str, Any]) -> None:
    """Write the prefs file owner-only from the first byte (it holds API keys),
    via a temp file so a crash mid write cannot leave a truncated file behind."""
    path = prefs_path()
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def get_key(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    return load().get("keys", {}).get(name) or None


def set_key(name: str, value: str) -> None:
    data = load()
    if value:
        data.setdefault("keys", {})[name] = value
    else:
        data.setdefault("keys", {}).pop(name, None)
    save(data)


def configured_keys() -> Dict[str, bool]:
    return {k: bool(get_key(k)) for k in PROVIDER_KEYS}


def set_integration(name: str, enabled: bool) -> None:
    data = load()
    data.setdefault("integrations", {})[name] = bool(enabled)
    save(data)
