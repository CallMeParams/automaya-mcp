"""gen.* commands: import AI generated results and tag them with provenance."""
from __future__ import annotations

from typing import Any, Dict

from ..registry import command
from . import assets as assets_handler
from ._util import require_maya

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore


def _tag(node: str, attr: str, value: str) -> None:
    try:
        if not cmds.attributeQuery(attr, node=node, exists=True):
            cmds.addAttr(node, longName=attr, dataType="string")
        cmds.setAttr("%s.%s" % (node, attr), value, type="string")
    except Exception:
        pass


@command("gen.import_result", mutates=True)
def import_result(path: str, name: str | None = None, group: bool = False, scale: float | None = None, freeze: bool = False, center: bool = False, provider: str = "", job_id: str = "", prompt: str = "") -> Dict[str, Any]:
    """Import a generated model and stamp automaya_provider / automaya_job (and
    automaya_prompt when given) string attributes on each top node."""
    require_maya()
    result = assets_handler.import_model(path=path, name=name, group=group, scale=scale, freeze=freeze, center=center)
    for node in result.get("top_nodes", []):
        if provider:
            _tag(node, "automaya_provider", provider)
        if job_id:
            _tag(node, "automaya_job", job_id)
        if prompt:
            _tag(node, "automaya_prompt", prompt[:512])
    result["provider"] = provider
    result["job_id"] = job_id
    if assets_handler.IMPORT_LOG:
        assets_handler.IMPORT_LOG[-1].update({"kind": "generated", "provider": provider, "job_id": job_id})
    return result
