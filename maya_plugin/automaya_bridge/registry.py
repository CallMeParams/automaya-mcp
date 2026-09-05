"""Command registry for the in-Maya bridge.

Handlers are plain functions decorated with ``@command("domain.name")``.
They receive keyword arguments taken from the request ``params`` and return
JSON serialisable data. ``mutates=True`` wraps the call in an undo chunk that
is rolled back if the handler raises, so a failed tool never leaves the scene
half edited.
"""
from __future__ import annotations

import inspect
import time
import traceback
from typing import Any, Callable, Dict

try:  # Maya is optional so the module can be unit tested outside Maya.
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover, exercised by the stub in tests
    cmds = None  # type: ignore


class CommandSpec:
    __slots__ = ("name", "func", "mutates", "doc", "signature")

    def __init__(self, name: str, func: Callable[..., Any], mutates: bool) -> None:
        self.name = name
        self.func = func
        self.mutates = mutates
        self.doc = inspect.getdoc(func) or ""
        self.signature = _describe_signature(func)


_REGISTRY: Dict[str, CommandSpec] = {}


def command(name: str, mutates: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register ``func`` under ``name``. Names are ``domain.action``."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            raise ValueError("duplicate bridge command %r" % name)
        _REGISTRY[name] = CommandSpec(name, func, mutates)
        return func

    return decorator


def get(name: str) -> CommandSpec | None:
    return _REGISTRY.get(name)


def names() -> list:
    return sorted(_REGISTRY)


def describe() -> Dict[str, Dict[str, Any]]:
    return {n: {"doc": s.doc, "mutates": s.mutates, "params": s.signature} for n, s in _REGISTRY.items()}


def _describe_signature(func: Callable[..., Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return out
    for p in sig.parameters.values():
        default = "required" if p.default is inspect.Parameter.empty else repr(p.default)
        out[p.name] = default
    return out


_CHUNK_COUNTER = 0


class UndoChunk:
    """Context manager that opens a named undo chunk and undoes on error.

    Maya drops empty chunks from the undo queue, so a handler that fails before
    touching the scene must not trigger ``cmds.undo()`` (that would revert the
    user's previous edit instead). Each chunk gets a unique name and the undo
    only runs when that name is what sits on top of the queue after closing.
    """

    def __init__(self, name: str) -> None:
        global _CHUNK_COUNTER
        _CHUNK_COUNTER += 1
        self.name = name
        self.chunk_name = "AutoMaya:%s (%d)" % (name, _CHUNK_COUNTER)

    def __enter__(self) -> UndoChunk:
        if cmds is not None:
            cmds.undoInfo(openChunk=True, chunkName=self.chunk_name)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if cmds is None:
            return False
        cmds.undoInfo(closeChunk=True)
        if exc_type is not None and self._chunk_is_on_queue():
            try:
                cmds.undo()
            except Exception:  # undo can legitimately fail on empty chunks
                pass
        return False

    def _chunk_is_on_queue(self) -> bool:
        try:
            top = cmds.undoInfo(query=True, undoName=True)
        except Exception:
            return True  # cannot tell, keep the old rollback behaviour
        if top is None:
            return True  # stub or very old Maya, same fallback
        return str(top) == self.chunk_name


def invoke(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Run a registered command and return a protocol response body."""
    from . import protocol  # local import keeps registry importable standalone

    spec = _REGISTRY.get(name)
    start = time.perf_counter()
    if spec is None:
        return protocol.make_error(
            None,
            "unknown command %r. Call core.list_commands to see what this plugin build supports." % name,
            code="unknown_command",
        )
    params = params or {}
    # Check the kwargs against the signature up front so a TypeError raised
    # inside the handler is reported as a real failure, not as bad params.
    try:
        inspect.signature(spec.func).bind(**params)
    except TypeError as exc:
        return protocol.make_error(
            None,
            "%s. Accepted params for %s: %s" % (exc, name, ", ".join(spec.signature) or "none"),
            code="bad_params",
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
        )
    except ValueError:
        pass  # no introspectable signature, let the call itself decide
    try:
        if spec.mutates:
            with UndoChunk(name):
                result = spec.func(**params)
        else:
            result = spec.func(**params)
        return protocol.make_success(None, result, (time.perf_counter() - start) * 1000.0)
    except Exception as exc:  # noqa: BLE001, we must report every failure
        return protocol.make_error(
            None,
            "%s: %s" % (type(exc).__name__, exc),
            traceback.format_exc(),
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
        )
