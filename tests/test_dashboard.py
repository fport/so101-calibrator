import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from so101_calibrator.dashboard.server import make_server

from .conftest import mock_env


@pytest.fixture
def dashboard():
    server, app = make_server(mock_env(), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    app.shutdown()
    server.shutdown()
    server.server_close()


def call(base, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json",
                                                                   **(headers or {})})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_index_and_static(dashboard):
    html = urllib.request.urlopen(dashboard + "/").read().decode()
    assert "SO-101 Calibrator" in html
    assert b"setHTML" in urllib.request.urlopen(dashboard + "/static/app.js").read()
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(dashboard + "/static/../server.py")


def test_rejects_foreign_host(dashboard):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(dashboard, "/api/state", headers={"Host": "evil.example:8101"})
    assert e.value.code == 403


def test_calibration_through_api(dashboard):
    state = call(dashboard, "/api/state")
    assert state["mock"] and "/dev/mock-follower" in state["ports"]
    call(dashboard, "/api/session", {"flow": "calibrate", "role": "follower", "note": "api"})
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        s = call(dashboard, "/api/state")["session"]
        if s["finished"]:
            break
        cur = s["current"]
        if cur["can"]["start"]:
            call(dashboard, "/api/session/action", {"action": "start"})
        elif cur["status"] == "running" and cur["id"] == "ranges":
            fills = [r["fill"] for r in cur["progress"].get("recorded", {}).values()]
            if fills and min(fills) >= 0.95:
                call(dashboard, "/api/session/action", {"action": "stop"})
        elif cur["can"]["accept"]:
            call(dashboard, "/api/session/action", {"action": "accept"})
        elif cur["status"] == "fail":
            pytest.fail(json.dumps(s["steps"]))
        time.sleep(0.05)
    assert s["finished"], json.dumps(s["current"])
    assert s["result"] == "pass" and s["record"] == 1
    assert s["arms"]["follower"]["joints"]["gripper"]["raw"] is not None

    versions = call(dashboard, "/api/arms/follower/versions")
    assert versions["arm"]["active"] == 1 and versions["versions"][0]["note"] == "api"
    call(dashboard, "/api/arms/follower/note", {"version": 1, "note": "renamed"})
    assert call(dashboard, "/api/arms/follower/versions/1")["note"] == "renamed"
    assert call(dashboard, "/api/arms/follower/diff?a=1")["rows"] == []
    exported = Path(call(dashboard, "/api/arms/follower/export", {})["exported"])
    assert exported.parts[-2:] == ("so_follower", "follower.json")
    call(dashboard, "/api/session/action", {"action": "close"})
    assert call(dashboard, "/api/state")["session"] is None


def test_bad_requests(dashboard):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(dashboard, "/api/session/action", {"action": "start"})
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError):
        call(dashboard, "/api/arms/ghost/versions")
