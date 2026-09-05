"""Provider registry: name -> class, plus lookup helpers."""
from __future__ import annotations

from typing import Any, Dict, List, Type

from .base import Provider3D, ProviderError
from .higgsfield import HiggsfieldProvider
from .hunyuan import HunyuanProvider
from .meshy import MeshyProvider
from .rodin import RodinProvider
from .tripo import TripoProvider

PROVIDERS: Dict[str, Type[Provider3D]] = {
    "tripo": TripoProvider,
    "meshy": MeshyProvider,
    "rodin": RodinProvider,
    "hunyuan": HunyuanProvider,
    "higgsfield": HiggsfieldProvider,
}

_INSTANCES: Dict[str, Provider3D] = {}


def get_provider(name: str) -> Provider3D:
    key = (name or "").strip().lower()
    aliases = {"hyper3d": "rodin", "tripo3d": "tripo", "hunyuan3d": "hunyuan", "tencent": "hunyuan"}
    key = aliases.get(key, key)
    cls = PROVIDERS.get(key)
    if cls is None:
        raise ProviderError("unknown provider %r. Available: %s" % (name, ", ".join(sorted(PROVIDERS))))
    inst = _INSTANCES.get(key)
    if inst is None:
        inst = cls()
        _INSTANCES[key] = inst
    return inst


def list_providers() -> List[Dict[str, Any]]:
    return [get_provider(n).describe() for n in PROVIDERS]


def reset() -> None:
    """Drop cached instances (tests swap env vars between cases)."""
    _INSTANCES.clear()
