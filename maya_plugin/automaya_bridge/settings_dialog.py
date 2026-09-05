"""Settings dialog for the AutoMaya console (PySide2).

Module level: ``PROVIDER_SPECS`` (what each provider needs) and
``test_provider(name, keys)`` (a cheap urllib ping against the provider's
status endpoint). Neither imports Qt, so they can be unit tested outside
Maya. ``build_dialog_class()`` imports PySide2 lazily and returns the
``SettingsDialog`` class; ``open_settings(parent)`` shows it.

Layout: a category list on the left (General, Connection, AI 3D APIs, Asset
Libraries, Live Link, Safe Mode) and a stacked page on the right. Save writes
through ``prefs``; Cancel discards everything.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Tuple

from . import prefs, protocol

TEST_TIMEOUT = 12.0
USER_AGENT = "automaya-maya/1.0"

# One entry per provider group in the AI 3D APIs page.
# fields: (env name, label, kind) with kind secret | text | url | combo:<a>|<b>
PROVIDER_SPECS: List[Dict[str, Any]] = [
    {
        "name": "tripo",
        "label": "Tripo 3D",
        "fields": [("TRIPO_API_KEY", "API key", "secret")],
        "test": "GET https://api.tripo3d.ai/v2/openapi/user/balance",
    },
    {
        "name": "meshy",
        "label": "Meshy",
        "fields": [("MESHY_API_KEY", "API key", "secret")],
        "test": "GET https://api.meshy.ai/openapi/v3/balance (falls back to v2 text-to-3d list)",
    },
    {
        "name": "rodin",
        "label": "Hyper3D Rodin",
        "fields": [
            ("RODIN_API_KEY", "Main site key", "secret"),
            ("FAL_KEY", "FAL key", "secret"),
            ("RODIN_MODE", "Route", "combo:auto|main|fal"),
        ],
        "test": "POST https://api.hyper3d.com/api/v2/check_balance (FAL: HEAD queue.fal.run)",
    },
    {
        "name": "hunyuan",
        "label": "Tencent Hunyuan3D",
        "fields": [
            ("HUNYUAN_SECRET_ID", "Secret id", "secret"),
            ("HUNYUAN_SECRET_KEY", "Secret key", "secret"),
            ("HUNYUAN_REGION", "Region", "text"),
            ("HUNYUAN_LOCAL_URL", "Local server URL", "url"),
        ],
        "test": "validates that a secret pair or a local URL is present (no signed call from Maya)",
    },
    {
        "name": "replicate",
        "label": "Replicate",
        "fields": [("REPLICATE_API_TOKEN", "API token", "secret")],
        "test": "GET https://api.replicate.com/v1/account",
    },
    {
        "name": "higgsfield",
        "label": "Higgsfield",
        "fields": [
            ("HIGGSFIELD_API_KEY", "API key", "secret"),
            ("HIGGSFIELD_API_SECRET", "API secret", "secret"),
            ("HIGGSFIELD_3D_ENDPOINT", "3D endpoint path", "text"),
        ],
        "test": "validates that key and secret are present",
    },
    {
        "name": "depth",
        "label": "Depth estimation",
        "fields": [("DEPTH_ENDPOINT", "Depth endpoint URL (Depth Anything / MiDaS)", "url")],
        "test": "GET the endpoint root",
        "no_toggle": True,
    },
]

LIBRARY_SPECS: List[Dict[str, Any]] = [
    {"name": "polyhaven", "label": "Poly Haven (free HDRIs, textures, models)", "fields": []},
    {"name": "sketchfab", "label": "Sketchfab", "fields": [("SKETCHFAB_API_TOKEN", "API token", "secret")]},
    {"name": "polypizza", "label": "Poly Pizza", "fields": [("POLYPIZZA_API_KEY", "API key", "secret")]},
]

CATEGORIES = ["General", "Connection", "AI 3D APIs", "Asset Libraries", "Live Link", "Safe Mode"]


def spec_for(name: str) -> Dict[str, Any] | None:
    for spec in PROVIDER_SPECS + LIBRARY_SPECS:
        if spec["name"] == name:
            return spec
    return None


def all_key_names() -> List[str]:
    names: List[str] = []
    for spec in PROVIDER_SPECS + LIBRARY_SPECS:
        for env, _label, _kind in spec["fields"]:
            names.append(env)
    return names


# network probe (urllib, runs on a worker thread inside Maya) --------------------
def _request(method: str, url: str, headers: Dict[str, str] | None = None, body: bytes | None = None, opener: Callable | None = None) -> Tuple[int, str]:
    """Return (http status, body text). Never raises for HTTP errors."""
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", USER_AGENT)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    do = opener or urllib.request.urlopen
    try:
        with do(req, timeout=TEST_TIMEOUT) as resp:
            code = int(getattr(resp, "status", None) or resp.getcode())
            text = resp.read(2000).decode("utf-8", "replace")
            return code, text
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read(2000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        return int(exc.code), text


def _verdict(code: int, text: str, provider: str) -> Tuple[bool, str]:
    if 200 <= code < 300:
        detail = ""
        try:
            data = json.loads(text)
            for key in ("balance", "credits", "username", "data"):
                if isinstance(data, dict) and key in data:
                    value = data[key]
                    if isinstance(value, dict):
                        value = value.get("balance", value.get("credits", value))
                    detail = " (%s: %s)" % (key, json.dumps(value)[:60])
                    break
        except ValueError:
            pass
        return True, "OK, HTTP %d%s" % (code, detail)
    if code in (401, 403):
        return False, "HTTP %d: %s rejected the key" % (code, provider)
    if code == 402:
        return False, "HTTP 402: key works but the %s balance is empty" % provider
    if code == 404:
        return False, "HTTP 404: the status endpoint moved; the key may still work"
    if code == 429:
        return False, "HTTP 429: rate limited, try again in a minute"
    return False, "HTTP %d: %s" % (code, text.strip()[:120] or "no body")


def test_provider(name: str, keys: Dict[str, str], opener: Callable | None = None) -> Tuple[bool, str]:
    """Ping ``name``'s cheapest status endpoint with ``keys`` (env name -> value).

    Returns (ok, message). Network errors become (False, message); nothing
    raises so the Qt worker can always report something.
    """
    keys = {k: (v or "").strip() for k, v in (keys or {}).items()}
    try:
        if name == "tripo":
            key = keys.get("TRIPO_API_KEY")
            if not key:
                return False, "TRIPO_API_KEY is empty"
            code, text = _request("GET", "https://api.tripo3d.ai/v2/openapi/user/balance", {"Authorization": "Bearer %s" % key}, opener=opener)
            return _verdict(code, text, "Tripo")
        if name == "meshy":
            key = keys.get("MESHY_API_KEY")
            if not key:
                return False, "MESHY_API_KEY is empty"
            headers = {"Authorization": "Bearer %s" % key}
            code, text = _request("GET", "https://api.meshy.ai/openapi/v3/balance", headers, opener=opener)
            if code == 404:
                code, text = _request("GET", "https://api.meshy.ai/openapi/v2/text-to-3d?page_size=1", headers, opener=opener)
            return _verdict(code, text, "Meshy")
        if name == "rodin":
            mode = keys.get("RODIN_MODE") or "auto"
            main_key = keys.get("RODIN_API_KEY")
            fal_key = keys.get("FAL_KEY")
            if mode == "fal" or (mode == "auto" and not main_key and fal_key):
                if not fal_key:
                    return False, "FAL_KEY is empty"
                code, text = _request("HEAD", "https://queue.fal.run/fal-ai/hyper3d/rodin", {"Authorization": "Key %s" % fal_key}, opener=opener)
                if code in (200, 204, 405):
                    return True, "OK, HTTP %d (fal.run reachable with this key)" % code
                return _verdict(code, text, "fal.run")
            if not main_key:
                return False, "RODIN_API_KEY is empty (or pick the FAL route)"
            code, text = _request("POST", "https://api.hyper3d.com/api/v2/check_balance", {"Authorization": "Bearer %s" % main_key, "Content-Type": "application/json"}, b"{}", opener=opener)
            if code == 404:
                code, text = _request("HEAD", "https://api.hyper3d.com/api/v2/rodin", {"Authorization": "Bearer %s" % main_key}, opener=opener)
                if code in (200, 204, 405):
                    return True, "OK, HTTP %d (hyper3d reachable with this key)" % code
            return _verdict(code, text, "Rodin")
        if name == "hunyuan":
            sid, skey, local = keys.get("HUNYUAN_SECRET_ID"), keys.get("HUNYUAN_SECRET_KEY"), keys.get("HUNYUAN_LOCAL_URL")
            if sid and skey:
                return True, "OK, secret id and key present (official route, region %s)" % (keys.get("HUNYUAN_REGION") or "ap-guangzhou")
            if local:
                if not local.startswith(("http://", "https://")):
                    return False, "HUNYUAN_LOCAL_URL must start with http:// or https://"
                code, text = _request("GET", local.rstrip("/") + "/", opener=opener)
                if code < 500:
                    return True, "OK, local server answered HTTP %d" % code
                return _verdict(code, text, "local Hunyuan3D")
            if sid or skey:
                return False, "both HUNYUAN_SECRET_ID and HUNYUAN_SECRET_KEY are needed"
            return False, "set a secret id and key, or a local server URL"
        if name == "replicate":
            token = keys.get("REPLICATE_API_TOKEN")
            if not token:
                return False, "REPLICATE_API_TOKEN is empty"
            code, text = _request("GET", "https://api.replicate.com/v1/account", {"Authorization": "Bearer %s" % token}, opener=opener)
            return _verdict(code, text, "Replicate")
        if name == "higgsfield":
            if keys.get("HIGGSFIELD_API_KEY") and keys.get("HIGGSFIELD_API_SECRET"):
                note = "" if keys.get("HIGGSFIELD_3D_ENDPOINT") else "; no 3D endpoint set, the provider stays inactive"
                return True, "OK, key and secret present%s" % note
            return False, "both HIGGSFIELD_API_KEY and HIGGSFIELD_API_SECRET are needed"
        if name == "depth":
            url = keys.get("DEPTH_ENDPOINT")
            if not url:
                return False, "DEPTH_ENDPOINT is empty"
            if not url.startswith(("http://", "https://")):
                return False, "DEPTH_ENDPOINT must start with http:// or https://"
            code, text = _request("GET", url, opener=opener)
            if code < 500:
                return True, "OK, endpoint answered HTTP %d" % code
            return _verdict(code, text, "depth endpoint")
        if name == "sketchfab":
            token = keys.get("SKETCHFAB_API_TOKEN")
            if not token:
                return False, "SKETCHFAB_API_TOKEN is empty"
            code, text = _request("GET", "https://api.sketchfab.com/v3/me", {"Authorization": "Token %s" % token}, opener=opener)
            return _verdict(code, text, "Sketchfab")
        if name == "polypizza":
            key = keys.get("POLYPIZZA_API_KEY")
            if not key:
                return False, "POLYPIZZA_API_KEY is empty"
            code, text = _request("GET", "https://api.poly.pizza/v1.1/search/chair?limit=1", {"x-auth-token": key}, opener=opener)
            return _verdict(code, text, "Poly Pizza")
        if name == "polyhaven":
            code, text = _request("GET", "https://api.polyhaven.com/types", opener=opener)
            return _verdict(code, text, "Poly Haven")
        return False, "no test defined for %r" % name
    except urllib.error.URLError as exc:
        return False, "network error: %s" % getattr(exc, "reason", exc)
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)


# Qt --------------------------------------------------------------------------------
_DIALOG_CLASS: Any = None


def build_dialog_class() -> Any:
    """Import PySide2 and return the SettingsDialog class (cached)."""
    global _DIALOG_CLASS
    if _DIALOG_CLASS is not None:
        return _DIALOG_CLASS
    from PySide2 import QtCore, QtWidgets  # type: ignore

    class _TestWorker(QtCore.QThread):
        finished_with = QtCore.Signal(str, bool, str)

        def __init__(self, name: str, keys: Dict[str, str], parent: Any = None) -> None:
            super().__init__(parent)
            self._name = name
            self._keys = keys

        def run(self) -> None:  # noqa: D401
            ok, message = test_provider(self._name, self._keys)
            self.finished_with.emit(self._name, ok, message)

    class SettingsDialog(QtWidgets.QDialog):
        def __init__(self, parent: Any = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("AutoMaya Settings")
            self.setMinimumSize(720, 480)
            self._prefs = prefs.load()
            self._keys: Dict[str, str] = dict(self._prefs.get("keys", {}))
            self._fields: Dict[str, Any] = {}
            self._toggles: Dict[str, Any] = {}
            self._test_labels: Dict[str, Any] = {}
            self._workers: List[Any] = []
            self._build()

        # layout ------------------------------------------------------------
        def _build(self) -> None:
            root = QtWidgets.QVBoxLayout(self)
            body = QtWidgets.QHBoxLayout()
            self.categories = QtWidgets.QListWidget()
            self.categories.setFixedWidth(150)
            self.stack = QtWidgets.QStackedWidget()
            builders = [self._page_general, self._page_connection, self._page_providers, self._page_libraries, self._page_livelink, self._page_safe]
            for title, builder in zip(CATEGORIES, builders):
                self.categories.addItem(title)
                self.stack.addWidget(self._scroll(builder()))
            self.categories.currentRowChanged.connect(self.stack.setCurrentIndex)
            self.categories.setCurrentRow(0)
            body.addWidget(self.categories)
            body.addWidget(self.stack, 1)
            root.addLayout(body, 1)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
            buttons.accepted.connect(self._save)
            buttons.rejected.connect(self.reject)
            root.addWidget(buttons)

        @staticmethod
        def _scroll(widget: Any) -> Any:
            area = QtWidgets.QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QtWidgets.QFrame.NoFrame)
            area.setWidget(widget)
            return area

        def _page_general(self) -> Any:
            page = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(page)
            self.auto_start = QtWidgets.QCheckBox("Start bridge when Maya launches")
            self.auto_start.setChecked(bool(self._prefs.get("auto_start", True)))
            form.addRow(self.auto_start)
            self.auto_events = QtWidgets.QCheckBox("Track scene changes automatically")
            self.auto_events.setChecked(bool(self._prefs.get("auto_events", True)))
            form.addRow(self.auto_events)
            self.event_port = QtWidgets.QSpinBox()
            self.event_port.setRange(1024, 65535)
            self.event_port.setValue(int(self._prefs.get("event_port", protocol.DEFAULT_EVENT_PORT)))
            form.addRow("Broadcast port", self.event_port)
            return page

        def _page_connection(self) -> Any:
            page = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(page)
            self.port = QtWidgets.QSpinBox()
            self.port.setRange(1024, 65535)
            self.port.setValue(int(self._prefs.get("port", protocol.DEFAULT_PORT)))
            form.addRow("Bridge port", self.port)
            host = QtWidgets.QLineEdit("127.0.0.1")
            host.setReadOnly(True)
            form.addRow("Host", host)
            note = QtWidgets.QLabel("The bridge only listens on loopback. Run the MCP server on the same machine, or tunnel the port with SSH.")
            note.setWordWrap(True)
            form.addRow(note)
            return page

        def _key_row(self, env: str, label: str, kind: str, form: Any) -> None:
            if kind.startswith("combo:"):
                combo = QtWidgets.QComboBox()
                combo.addItems(kind[len("combo:"):].split("|"))
                current = self._keys.get(env, "")
                if current:
                    idx = combo.findText(current)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                self._fields[env] = combo
                form.addRow(label, combo)
                return
            row = QtWidgets.QHBoxLayout()
            field = QtWidgets.QLineEdit()
            field.setText(self._keys.get(env, ""))
            placeholder = "env %s or paste here" % env
            if not self._keys.get(env) and prefs.get_key(env):
                placeholder = "set from environment"
            field.setPlaceholderText(placeholder)
            row.addWidget(field, 1)
            if kind == "secret":
                field.setEchoMode(QtWidgets.QLineEdit.Password)
                show = QtWidgets.QToolButton()
                show.setText("Show")
                show.setCheckable(True)
                show.toggled.connect(lambda on, f=field, b=show: (f.setEchoMode(QtWidgets.QLineEdit.Normal if on else QtWidgets.QLineEdit.Password), b.setText("Hide" if on else "Show")))
                row.addWidget(show)
            self._fields[env] = field
            form.addRow(label, row)

        def _group(self, spec: Dict[str, Any]) -> Any:
            box = QtWidgets.QGroupBox(spec["label"])
            outer = QtWidgets.QVBoxLayout(box)
            if not spec.get("no_toggle"):
                toggle = QtWidgets.QCheckBox("Enabled")
                toggle.setChecked(bool(self._prefs.get("integrations", {}).get(spec["name"], False)))
                self._toggles[spec["name"]] = toggle
                outer.addWidget(toggle)
            form = QtWidgets.QFormLayout()
            for env, label, kind in spec["fields"]:
                self._key_row(env, label, kind, form)
            outer.addLayout(form)
            row = QtWidgets.QHBoxLayout()
            test = QtWidgets.QPushButton("Test")
            test.clicked.connect(lambda _=False, n=spec["name"]: self._run_test(n))
            status = QtWidgets.QLabel(spec.get("test", ""))
            status.setWordWrap(True)
            status.setStyleSheet("color: #999")
            self._test_labels[spec["name"]] = status
            row.addWidget(test)
            row.addWidget(status, 1)
            outer.addLayout(row)
            return box

        def _page_providers(self) -> Any:
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            for spec in PROVIDER_SPECS:
                layout.addWidget(self._group(spec))
            layout.addStretch(1)
            return page

        def _page_libraries(self) -> Any:
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            for spec in LIBRARY_SPECS:
                layout.addWidget(self._group(spec))
            layout.addStretch(1)
            return page

        def _page_livelink(self) -> Any:
            page = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(page)
            self.live_port = QtWidgets.QSpinBox()
            self.live_port.setRange(1024, 65535)
            self.live_port.setValue(int(self._prefs.get("event_port", protocol.DEFAULT_EVENT_PORT)))
            self.live_port.valueChanged.connect(self.event_port.setValue)
            self.event_port.valueChanged.connect(self.live_port.setValue)
            form.addRow("Event port", self.live_port)
            self.transform_only = QtWidgets.QCheckBox("Broadcast transforms only (default)")
            self.transform_only.setChecked(bool(self._prefs.get("transform_only", True)))
            form.addRow(self.transform_only)
            return page

        def _page_safe(self) -> Any:
            page = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(page)
            self.safe_mode = QtWidgets.QCheckBox("Safe mode (block arbitrary Python from the agent)")
            self.safe_mode.setChecked(bool(self._prefs.get("safe_mode", False)))
            form.addRow(self.safe_mode)
            note = QtWidgets.QLabel("With safe mode on, maya_run_python and the REPL from the agent are refused; typed tools still work.")
            note.setWordWrap(True)
            form.addRow(note)
            return page

        # behaviour ---------------------------------------------------------
        def _value(self, env: str) -> str:
            widget = self._fields.get(env)
            if widget is None:
                return ""
            if isinstance(widget, QtWidgets.QComboBox):
                text = widget.currentText().strip()
                return "" if text == "auto" else text
            return widget.text().strip()

        def _keys_for(self, name: str) -> Dict[str, str]:
            spec = spec_for(name) or {"fields": []}
            out: Dict[str, str] = {}
            for env, _label, _kind in spec["fields"]:
                out[env] = self._value(env) or (prefs.get_key(env) or "")
            return out

        def _run_test(self, name: str) -> None:
            label = self._test_labels[name]
            label.setStyleSheet("color: #ffd27f")
            label.setText("testing...")
            worker = _TestWorker(name, self._keys_for(name), self)
            worker.finished_with.connect(self._test_done)
            worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
            self._workers.append(worker)
            worker.start()

        def _test_done(self, name: str, ok: bool, message: str) -> None:
            label = self._test_labels.get(name)
            if label is None:
                return
            label.setStyleSheet("color: %s" % ("#8ee38e" if ok else "#ff7b7b"))
            label.setText(message)

        def _save(self) -> None:
            data = prefs.load()
            data["auto_start"] = self.auto_start.isChecked()
            data["auto_events"] = self.auto_events.isChecked()
            data["event_port"] = int(self.event_port.value())
            data["port"] = int(self.port.value())
            data["safe_mode"] = self.safe_mode.isChecked()
            data["transform_only"] = self.transform_only.isChecked()
            prefs.save(data)
            for name, toggle in self._toggles.items():
                prefs.set_integration(name, toggle.isChecked())
            for env in self._fields:
                prefs.set_key(env, self._value(env))
            self.accept()

    _DIALOG_CLASS = SettingsDialog
    return SettingsDialog


def open_settings(parent: Any = None) -> Any:
    """Show the dialog modally; returns the QDialog result code."""
    dialog = build_dialog_class()(parent)
    return dialog.exec_()
