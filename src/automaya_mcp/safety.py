"""Server side code validation for maya_execute_python and maya_execute_mel.

The plugin enforces its own safe mode; this layer gives the agent a fast,
descriptive rejection before the code ever reaches Maya, and lets the server
be locked down independently with AUTOMAYA_SAFE_MODE=1.

Safe mode is a guard rail against careless agent code, not a sandbox: a static
check on Python cannot be made airtight. The rules mirror the plugin's
(handlers/core.py) so both sides reject the same things.
"""
from __future__ import annotations

import ast
import os
import re
from typing import List

BLOCKED_MODULES = {
    "os", "subprocess", "ctypes", "shutil", "socket", "http", "urllib", "ftplib", "smtplib", "pickle", "marshal",
    "importlib", "builtins", "sys", "pathlib", "io", "runpy", "code", "codeop", "pty", "pydoc", "pkgutil", "inspect",
    "gc", "types", "unittest", "codecs", "tarfile", "zipfile", "sqlite3", "shelve", "dbm", "webbrowser", "asyncio",
    "multiprocessing", "xmlrpc", "telnetlib", "poplib", "imaplib", "ssl", "socketserver", "glob", "tempfile", "signal",
}
BLOCKED_NAMES = {"eval", "exec", "compile", "__import__", "open", "getattr", "setattr", "delattr", "vars", "globals", "locals", "breakpoint", "input", "__builtins__", "__loader__", "__spec__"}
BLOCKED_ATTRS = {
    "system", "popen", "spawn", "spawnl", "spawnv", "remove", "rmtree", "unlink", "rmdir", "kill", "chmod",
    "write_text", "write_bytes", "eval", "python", "evalDeferred", "executeDeferred", "executeInMainThreadWithResult",
    "scriptJob", "scriptNode", "sysFile", "source", "commandPort",
}
BLOCKED_MEL = re.compile(r"\b(system|sysFile|python|exec|eval|evalDeferred|evalEcho|source|scriptJob|scriptNode|fopen|fremove|putenv|commandPort|loadPlugin|unloadPlugin|cmdFileOutput|launchImageEditor)\b")


def safe_mode_enabled() -> bool:
    return os.environ.get("AUTOMAYA_SAFE_MODE", "0") == "1"


def validate(code: str) -> List[str]:
    """Return a list of violations (empty means the code is acceptable)."""
    problems: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return ["syntax error: %s" % exc]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BLOCKED_MODULES:
                    problems.append("import of %s is blocked in safe mode" % alias.name)
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in BLOCKED_MODULES:
                problems.append("import from %s is blocked in safe mode" % node.module)
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            problems.append("%s is blocked in safe mode" % node.id)
        elif isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_ATTRS:
                problems.append("attribute .%s is blocked in safe mode" % node.attr)
            elif node.attr.startswith("__") and node.attr.endswith("__"):
                problems.append("dunder attribute .%s is blocked in safe mode" % node.attr)
    return problems


def validate_mel(code: str) -> List[str]:
    """Violations for a MEL snippet: shell, file, plugin and Python escape hatches."""
    return ["MEL command %s is blocked in safe mode" % m for m in sorted(set(BLOCKED_MEL.findall(code)))]
