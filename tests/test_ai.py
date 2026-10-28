import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from so101_calibrator import ai as AI
from so101_calibrator.cli import main
from so101_calibrator.mock import MockRig
from so101_calibrator.store import Store

from .conftest import calibrate

ANSWER = ["Plug ", "in ", "12 V."]


@pytest.fixture
def fake_server(monkeypatch):
    """Speaks enough of the Ollama (/api/*, NDJSON) and OpenAI (/v1/*, SSE) streaming protocols."""
    seen: list[dict] = []
    options = {"reject_tools": False}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, payload: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._send(200, json.dumps({"models": [{"name": "tiny:1b", "model": "tiny:1b"}]}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            if self.path == "/api/chat":
                if options["reject_tools"] and body.get("tools"):
                    self._send(400, b'{"error":"registry.ollama.ai/library/tiny:1b does not support tools"}')
                    return
                chunk = {"model": "tiny:1b", "created_at": "2026-01-01T00:00:00Z", "done": False}
                lines = [{**chunk, "message": {"role": "assistant", "content": w}} for w in ANSWER]
                lines.append({**chunk, "message": {"role": "assistant", "content": ""}, "done": True,
                              "done_reason": "stop", "total_duration": 1000, "prompt_eval_count": 5,
                              "eval_count": 3, "eval_duration": 1})
                self._send(200, b"".join(json.dumps(x).encode() + b"\n" for x in lines), "application/x-ndjson")
            else:  # /v1/chat/completions
                base = {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": body["model"]}
                events = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": w},
                                                "finish_reason": None}]} for w in ("OK ", "from ", "openai")]
                events.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                events.append({**base, "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 3,
                                                                "total_tokens": 4}})
                sse = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events) + b"data: [DONE]\n\n"
                self._send(200, sse, "text/event-stream")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("OLLAMA_HOST", url)
    monkeypatch.setenv("SO101CAL_AI_URL", url + "/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    yield SimpleNamespace(url=url, seen=seen, options=options)
    srv.shutdown()


def test_parse_spec():
    assert AI.parse_spec("ollama") == ("ollama", "")
    assert AI.parse_spec("ollama:qwen3.5:9b") == ("ollama", "qwen3.5:9b")
    assert AI.parse_spec("anthropic") == ("claude", "")
    assert AI.Assistant("claude").model == AI.CLAUDE_MODEL
    with pytest.raises(AI.AIError):
        AI.parse_spec("gpt")
    with pytest.raises(AI.AIError):
        AI.parse_spec("openai")


def test_spec_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("SO101CAL_AI", raising=False)
    assert AI.load_spec(tmp_path) is None
    AI.save_spec(tmp_path, "ollama")
    assert AI.load_spec(tmp_path) == "ollama"
    monkeypatch.setenv("SO101CAL_AI", "claude")
    assert AI.load_spec(tmp_path) == "claude"
    assert AI.load_spec(tmp_path, "openai:x") == "openai:x"
    assert AI.load_spec(tmp_path, "off") is None


def test_describe_session_snapshot(store):
    session = calibrate(store, MockRig.create("voltage:5.0"), "follower", port_known=True)
    state = AI.describe(session.snapshot(), [])
    power = next(s for s in state["steps"] if s["step"] == "Check supply voltage")
    assert power["status"] == "fail" and power["problems"][0]["fix"]
    assert "follower" in state["joints"]
    json.dumps(state)  # must be serializable as the prompt


def test_ollama_agent_streams_with_tools_and_thinking_off(fake_server, store):
    pytest.importorskip("strands")
    calibrate(store, MockRig.create(), "follower", port_known=True)
    a = AI.Assistant("ollama", store=store)
    partial: list[str] = []
    assert a.ask("", {"flow": "calibrate"}, on_text=partial.append, lang="tr") == "Plug in 12 V."
    assert a.model == "tiny:1b" and partial[0] == "Plug " and partial[-1] == "Plug in 12 V."
    body = fake_server.seen[-1]
    assert body["think"] is False
    assert {t["function"]["name"] for t in body["tools"]} == {"calibration_history", "compare_versions"}
    assert "in Turkish" in body["messages"][-1]["content"]  # empty question: answer in the UI language


def test_ollama_model_without_tool_support(fake_server, store):
    pytest.importorskip("strands")
    fake_server.options["reject_tools"] = True
    assert AI.Assistant("ollama:tiny:1b", store=store).ask("hi", {}) == "Plug in 12 V."
    assert not fake_server.seen[-1].get("tools")


def test_history_tools_are_read_only(store):
    pytest.importorskip("strands")
    calibrate(store, MockRig.create(), "follower", port_known=True)
    calibrate(store, MockRig.create(), "follower", port_known=True)
    tools = {t.tool_name: t for t in AI.history_tools(store)}
    assert set(tools) == {"calibration_history", "compare_versions"}
    versions = json.loads(tools["calibration_history"]("follower"))
    assert [v["version"] for v in versions] == [1, 2] and versions[1]["active"]
    assert isinstance(json.loads(tools["compare_versions"]("follower", 1, 2)), list)
    assert tools["calibration_history"]("nope").startswith("error")


def test_openai_compatible(fake_server):
    pytest.importorskip("strands")
    assert AI.Assistant("openai:local-model").ask("hi", {}) == "OK from openai"
    assert fake_server.seen[-1]["model"] == "local-model"


def test_unreachable_server(monkeypatch):
    pytest.importorskip("strands")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    with pytest.raises(AI.AIError, match="ollama serve"):
        AI.Assistant("ollama:x").ask("", {})


def test_claude_request_shape(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    calls = {}

    class FakeStream:
        text_stream = iter(["Check ", "the cable."])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text="Check the cable.")])

    class FakeClient:
        def __init__(self, **kw):
            self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

        def stream(self, **kw):
            calls.update(kw)
            return FakeStream()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    assert AI.Assistant("claude").ask("", {}) == "Check the cable."
    assert calls["model"] == AI.CLAUDE_MODEL and calls["fallbacks"] == "default"
    assert calls["output_config"] == {"effort": "low"} and "server-side-fallback-2026-07-01" in calls["betas"]


def test_cli_ai_commands(capsys, fake_server, monkeypatch):
    pytest.importorskip("strands")
    monkeypatch.delenv("SO101CAL_AI", raising=False)
    assert main(["ai"]) == 0 and "off" in capsys.readouterr().out
    assert main(["ai", "use", "ollama:tiny:1b"]) == 0
    assert main(["ai"]) == 0 and "ollama:tiny:1b" in capsys.readouterr().out
    assert main(["ai", "ask", "what", "next?"]) == 0
    assert "Plug in 12 V." in capsys.readouterr().out
    assert main(["ai", "use", "nope"]) == 2
    assert main(["ai", "off"]) == 0
    assert main(["ai", "ask", "x"]) == 2


def test_store_fixture_is_isolated(store):
    assert isinstance(store, Store) and not store.arms()
