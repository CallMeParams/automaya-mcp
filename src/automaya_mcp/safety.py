"""Server side code validation for maya_execute_python.

The plugin enforces its own safe mode; this layer gives the agent a fast,
descriptive rejection before the code ever reaches Maya, and lets the server
be locked down independently with AUTOMAYA_SAFE_MODE=1.
"""
from __future__ import annotations

import ast
import os
from typing import List

BLOCKED_MODULES = {"subprocess", "ctypes", "shutil", "socket", "http", "urllib", "ftplib", "smtplib", "pickle", "marshal", "os"}
BLOCKED_CALLS = {"eval", "exec", "compile", "__import__"}
BLOCKED_ATTRS = {"system", "popen", "remove", "rmtree", "unlink", "kill"}


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
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
            problems.append("%s() is blocked in safe mode" % node.func.id)
        elif isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRS:
            problems.append("attribute .%s is blocked in safe mode" % node.attr)
    return problems
