"""Settings dialog: PROVIDER_SPECS table and probe() with a fake urllib opener.

PySide2 is not installed here; the module imports Qt only inside
``build_dialog_class()`` so everything below runs without it.
"""
from __future__ import annotations

import io
import json
import urllib.error
from typing import Any, Dict, List

import pytest

from automaya_bridge import prefs, settings_dialog
from automaya_bridge.settings_dialog import LIBRARY_SPECS, PROVIDER_SPECS, all_key_names, spec_for

probe = settings_dialog.test_provider  # aliased so pytest does not collect it as a test


class _Resp:
    def __init__(self, code: int, body: Any) -> None:
        self.status = code
        self._body = (json.dumps(body) if not isinstance(body, (str, bytes)) else body)
        if isinstance(self._body, str):
            self._body = self._body.encode("utf-8")

    def read(self, n: int = -1) -> bytes:
        return self._body[:n] if n > 0 else self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class FakeOpener:
    """Records requests; responds from a (method, url prefix) -> (code, body) table."""

    def __init__(self, routes: Dict[tuple, tuple]) -> None:
        self.routes = routes
        self.calls: List[Any] = []

    def __call__(self, req: Any, timeout: float = 0) -> _Resp:
        self.calls.append(req)
        for (method, prefix), (code, body) in self.routes.items():
            if req.get_method() == method and req.full_url.startswith(prefix):
                if code >= 400:
                    raise urllib.error.HTTPError(req.full_url, code, "err", {}, io.BytesIO(json.dumps(body).encode()))
                return _Resp(code, body)
        raise urllib.error.URLError("no route for %s %s" % (req.get_method(), req.full_url))


# table -----------------------------------------------------------------------------
def test_module_imports_without_qt():
    import sys

    assert "PySide2" not in sys.modules
    assert settings_dialog._DIALOG_CLASS is None


def test_provider_specs_shape():
    names = [s["name"] for s in PROVIDER_SPECS]
    assert names == ["tripo", "meshy", "rodin", "hunyuan", "replicate", "higgsfield", "depth"]
    for spec in PROVIDER_SPECS + LIBRARY_SPECS:
        assert spec["label"]
        for env, label, kind in spec["fields"]:
            assert env.isupper() and label
            assert kind in ("secret", "text", "url") or kind.startswith("combo:")
    rodin = spec_for("rodin")
    assert [f[0] for f in rodin["fields"]] == ["RODIN_API_KEY", "FAL_KEY", "RODIN_MODE"]
    assert rodin["fields"][2][2] == "combo:auto|main|fal"
    hunyuan = spec_for("hunyuan")
    assert [f[0] for f in hunyuan["fields"]] == ["HUNYUAN_SECRET_ID", "HUNYUAN_SECRET_KEY", "HUNYUAN_REGION", "HUNYUAN_LOCAL_URL"]
    assert spec_for("depth")["no_toggle"] is True
    assert spec_for("nope") is None


def test_every_field_is_a_known_pref_key():
    for env in all_key_names():
        if env == "RODIN_MODE":
            continue
        assert env in prefs.PROVIDER_KEYS, env
    for name in ("replicate", "higgsfield", "hunyuan", "tripo", "meshy", "rodin", "polyhaven", "sketchfab", "polypizza"):
        assert name in prefs.DEFAULTS["integrations"]


# test_provider --------------------------------------------------------------------
def test_empty_keys_fail_fast():
    opener = FakeOpener({})
    for name, env in (("tripo", "TRIPO_API_KEY"), ("meshy", "MESHY_API_KEY"), ("replicate", "REPLICATE_API_TOKEN"), ("depth", "DEPTH_ENDPOINT")):
        ok, msg = probe(name, {}, opener=opener)
        assert not ok and env in msg
    assert not opener.calls
    ok, msg = probe("unknown", {"X": "y"}, opener=opener)
    assert not ok and "no test" in msg


def test_tripo_balance_ok_and_rejected():
    opener = FakeOpener({("GET", "https://api.tripo3d.ai/v2/openapi/user/balance"): (200, {"code": 0, "data": {"balance": 120}})})
    ok, msg = probe("tripo", {"TRIPO_API_KEY": "tk"}, opener=opener)
    assert ok and msg.startswith("OK, HTTP 200") and "120" in msg
    assert opener.calls[0].get_header("Authorization") == "Bearer tk"
    opener = FakeOpener({("GET", "https://api.tripo3d.ai/"): (401, {"message": "bad"})})
    ok, msg = probe("tripo", {"TRIPO_API_KEY": "bad"}, opener=opener)
    assert not ok and "401" in msg and "rejected" in msg


def test_meshy_falls_back_to_v2_list():
    opener = FakeOpener({
        ("GET", "https://api.meshy.ai/openapi/v3/balance"): (404, {}),
        ("GET", "https://api.meshy.ai/openapi/v2/text-to-3d?page_size=1"): (200, []),
    })
    ok, msg = probe("meshy", {"MESHY_API_KEY": "mk"}, opener=opener)
    assert ok and len(opener.calls) == 2
    assert opener.calls[1].full_url.endswith("page_size=1")


def test_rodin_main_and_fal_routes():
    opener = FakeOpener({("POST", "https://api.hyper3d.com/api/v2/check_balance"): (200, {"balance": 5})})
    ok, msg = probe("rodin", {"RODIN_API_KEY": "rk", "RODIN_MODE": ""}, opener=opener)
    assert ok and "balance" in msg and opener.calls[0].get_method() == "POST"
    opener = FakeOpener({("HEAD", "https://queue.fal.run/fal-ai/hyper3d/rodin"): (405, "")})
    ok, msg = probe("rodin", {"FAL_KEY": "vibecoding", "RODIN_MODE": "auto"}, opener=opener)
    assert ok and opener.calls[0].get_header("Authorization") == "Key vibecoding"
    ok, msg = probe("rodin", {"RODIN_MODE": "fal"}, opener=FakeOpener({}))
    assert not ok and "FAL_KEY" in msg
    ok, msg = probe("rodin", {"RODIN_MODE": "main", "FAL_KEY": "x"}, opener=FakeOpener({}))
    assert not ok and "RODIN_API_KEY" in msg


def test_hunyuan_validates_locally():
    opener = FakeOpener({})
    ok, msg = probe("hunyuan", {"HUNYUAN_SECRET_ID": "a", "HUNYUAN_SECRET_KEY": "b", "HUNYUAN_REGION": "ap-singapore"}, opener=opener)
    assert ok and "ap-singapore" in msg and not opener.calls
    ok, msg = probe("hunyuan", {"HUNYUAN_SECRET_ID": "a"}, opener=opener)
    assert not ok and "HUNYUAN_SECRET_KEY" in msg
    ok, msg = probe("hunyuan", {}, opener=opener)
    assert not ok
    opener = FakeOpener({("GET", "http://localhost:8081/"): (404, {})})
    ok, msg = probe("hunyuan", {"HUNYUAN_LOCAL_URL": "http://localhost:8081"}, opener=opener)
    assert ok and "404" in msg
    ok, msg = probe("hunyuan", {"HUNYUAN_LOCAL_URL": "localhost:8081"}, opener=opener)
    assert not ok and "http://" in msg


def test_replicate_account_and_network_error():
    opener = FakeOpener({("GET", "https://api.replicate.com/v1/account"): (200, {"type": "user", "username": "adam"})})
    ok, msg = probe("replicate", {"REPLICATE_API_TOKEN": "r8_x"}, opener=opener)
    assert ok and "adam" in msg and opener.calls[0].get_header("Authorization") == "Bearer r8_x"
    ok, msg = probe("replicate", {"REPLICATE_API_TOKEN": "r8_x"}, opener=FakeOpener({}))
    assert not ok and "network error" in msg


def test_higgsfield_depth_and_libraries():
    ok, msg = probe("higgsfield", {"HIGGSFIELD_API_KEY": "k", "HIGGSFIELD_API_SECRET": "s"}, opener=FakeOpener({}))
    assert ok and "no 3D endpoint" in msg
    ok, msg = probe("higgsfield", {"HIGGSFIELD_API_KEY": "k"}, opener=FakeOpener({}))
    assert not ok
    opener = FakeOpener({("GET", "http://127.0.0.1:5000/"): (200, "depth ready")})
    ok, msg = probe("depth", {"DEPTH_ENDPOINT": "http://127.0.0.1:5000/"}, opener=opener)
    assert ok
    opener = FakeOpener({("GET", "https://api.sketchfab.com/v3/me"): (200, {"username": "me"})})
    ok, msg = probe("sketchfab", {"SKETCHFAB_API_TOKEN": "t"}, opener=opener)
    assert ok and opener.calls[0].get_header("Authorization") == "Token t"
    opener = FakeOpener({("GET", "https://api.poly.pizza/v1.1/search/"): (200, {"results": []})})
    ok, msg = probe("polypizza", {"POLYPIZZA_API_KEY": "p"}, opener=opener)
    assert ok and opener.calls[0].get_header("X-auth-token") == "p"
    opener = FakeOpener({("GET", "https://api.polyhaven.com/types"): (200, ["hdris"])})
    assert probe("polyhaven", {}, opener=opener)[0]


def test_rate_limit_and_server_error_messages():
    opener = FakeOpener({("GET", "https://api.tripo3d.ai/"): (429, {})})
    ok, msg = probe("tripo", {"TRIPO_API_KEY": "k"}, opener=opener)
    assert not ok and "rate limited" in msg
    opener = FakeOpener({("GET", "https://api.tripo3d.ai/"): (500, {"error": "boom"})})
    ok, msg = probe("tripo", {"TRIPO_API_KEY": "k"}, opener=opener)
    assert not ok and msg.startswith("HTTP 500")


def test_build_dialog_class_needs_qt():
    with pytest.raises(ImportError):
        settings_dialog.build_dialog_class()
