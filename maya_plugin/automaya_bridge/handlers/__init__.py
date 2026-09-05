"""Domain handler modules. Each registers commands via ``registry.command``.

``load_all`` imports every module so the registry is populated once. Modules
that need an optional Maya plugin (mtoa, bullet, bifrost) still import fine;
they load the plugin lazily inside the handler and raise a clear error if it
is missing.
"""
from __future__ import annotations

import importlib
import logging

MODULES = [
    "core",
    "scene",
    "modeling",
    "materials",
    "rigging_animation",
    "previs",
    "sim_vfx",
    "arnold",
    "assets",
    "generation",
    "intelligence",
    "livelink",
    "introspect",
    "procgen",
    "light",
    "lookdev",
    "photo",
]

_loaded = False


def load_all() -> None:
    global _loaded
    if _loaded:
        return
    for name in MODULES:
        try:
            importlib.import_module("%s.%s" % (__name__, name))
        except Exception as exc:  # keep the bridge alive even if one domain is broken
            logging.getLogger("automaya").error("handler module %s failed to load: %s", name, exc)
    _loaded = True
