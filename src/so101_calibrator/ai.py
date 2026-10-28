"""Optional AI helper: explains the current step / failed checks and answers questions.

Off unless configured. A spec is `provider[:model]`:

    claude[:model]        Anthropic API (extra `ai`), key from ANTHROPIC_API_KEY or `ant auth login`
    ollama[:model]        local Ollama ($OLLAMA_HOST, default http://localhost:11434)
    openai:model          any OpenAI-compatible server: $SO101CAL_AI_URL (e.g. LM Studio
                          http://localhost:1234/v1, llama.cpp, vLLM) or api.openai.com with $OPENAI_API_KEY

Set it with `so101-cal ai use <spec>` (stored in <store>/config.json), `--ai <spec>` or $SO101CAL_AI.
Only the calibration state shown in the tool (step, check results, joint readings) is sent.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from .profiles import JOINTS

PROVIDERS = ("claude", "ollama", "openai")
CLAUDE_MODEL = "claude-opus-5-5"

SYSTEM = """You are the assistant inside so101-calibrator, a tool that calibrates SO-101 robot arms
(LeRobot compatible, Feetech STS3215 servos, 12-bit encoders 0..4095, 6 joints: shoulder_pan, shoulder_lift,
elbow_flex, wrist_flex, wrist_roll, gripper; follower on a 12 V supply, leader on 5 V).

Calibration steps: find the USB port (unplug/replug), scan motor ids 1-6 at 1 Mbps, check supply voltage,
middle pose (homing offset = position - 2047, so every joint reads ~2047 in the middle pose), record ranges
of motion (move every joint except wrist_roll to both mechanical stops), record joint directions (compared
with the paired arm to catch a motor or horn mounted backwards), save (write to EEPROM, store a version,
export to the LeRobot cache). Verify checks the EEPROM and the middle pose; the teleop test mirrors
leader -> follower and measures tracking error.

You get the tool's current state as JSON. Answer the user's question about it.
- Be brief: at most 5 short lines or bullets. Concrete physical actions first ("unplug...", "move...").
- Answer in the language the user writes in. If the question is empty, explain the most important
  warning or failure in the state and how to fix it; if there is none, say what to do next in one line.
- Use only the numbers in the state; do not invent register values or readings.
- After fixing a failed step the user presses Retry (Enter) to repeat just that step; never tell them to
  restart the whole calibration. A warning can be accepted to continue.
- Safety: unplug power before touching wiring; keep hands clear when follower torque is on."""


class AIError(RuntimeError):
    pass


def parse_spec(spec: str) -> tuple[str, str]:
    provider, _, model = spec.strip().partition(":")
    provider = {"anthropic": "claude", "lmstudio": "openai", "local": "openai"}.get(provider, provider)
    if provider not in PROVIDERS:
        raise AIError(f"unknown AI provider '{provider}' (use {', '.join(PROVIDERS)})")
    if provider == "openai" and not model:
        raise AIError("openai needs a model: openai:<model>")
    return provider, model


# --- configuration ---------------------------------------------------------------------------------
def config_path(store_root) -> os.PathLike:
    return store_root / "config.json"


def load_spec(store_root, override: str | None = None) -> str | None:
    if override:
        return None if override == "off" else override
    if os.environ.get("SO101CAL_AI"):
        return os.environ["SO101CAL_AI"]
    p = config_path(store_root)
    if p.exists():
        return json.loads(p.read_text()).get("ai")
    return None


def save_spec(store_root, spec: str | None) -> None:
    p = config_path(store_root)
    data = json.loads(p.read_text()) if p.exists() else {}
    data["ai"] = spec
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


# --- state -> prompt ---------------------------------------------------------------------------------
def describe(snapshot: dict | None, arms: list[dict] | None = None) -> dict:
    """The compact part of a session snapshot the model needs (keeps the prompt small)."""
    out: dict = {"stored_arms": [{k: a.get(k) for k in ("arm_id", "role", "active", "versions")}
                                 for a in (arms or [])]}
    if not snapshot:
        return out
    cur = snapshot.get("current")
    out["flow"] = snapshot["flow"]
    out["result"] = snapshot.get("result")
    out["steps"] = [{"step": s["label"], "status": s["status"],
                     "problems": [{k: c[k] for k in ("status", "message", "fix", "joint") if c.get(k)}
                                  for c in s["checks"] if c["status"] != "pass"]}
                    for s in snapshot["steps"]]
    if cur:
        out["current_step"] = {"step": cur["label"], "status": cur["status"], "instructions": cur["instructions"]}
    out["joints"] = {role: {j: {k: v for k, v in a["joints"][j].items() if v is not None} for j in JOINTS}
                     for role, a in snapshot.get("arms", {}).items() if a.get("connected")}
    out["log"] = [e["msg"] for e in snapshot.get("events", [])[-8:]]
    return out


# --- providers ---------------------------------------------------------------------------------------
@dataclass
class Assistant:
    spec: str
    history: list[dict] = field(default_factory=list)  # user/assistant turns of this conversation
    timeout: float = 120.0

    def __post_init__(self):
        self.provider, self.model = parse_spec(self.spec)
        if self.provider == "claude" and not self.model:
            self.model = CLAUDE_MODEL

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider

    def ask(self, question: str, state: dict, on_text: Callable[[str], None] | None = None) -> str:
        """Answer `question` about `state`. `on_text` gets the answer so far while it streams in."""
        user = f"State:\n{json.dumps(state, separators=(',', ':'))}\n\nQuestion: {question.strip() or '(none)'}"
        messages = [*self.history[-6:], {"role": "user", "content": user}]
        answer = getattr(self, f"_ask_{self.provider}")(messages, on_text or (lambda _: None)).strip()
        # keep only the question text in history; the next turn carries the fresh state
        self.history += [{"role": "user", "content": question.strip() or "Explain the current problem."},
                         {"role": "assistant", "content": answer}]
        return answer

    def _ask_claude(self, messages: list[dict], on_text) -> str:
        try:
            import anthropic
        except ImportError as e:
            raise AIError('Claude needs the Anthropic SDK: pip install "so101-calibrator[ai]"') from e
        try:
            client = anthropic.Anthropic(timeout=self.timeout)
            with client.beta.messages.stream(
                model=self.model, max_tokens=2048, system=SYSTEM, messages=messages,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            ) as stream:
                text = ""
                for chunk in stream.text_stream:
                    text += chunk
                    on_text(text)
                resp = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise AIError("Anthropic API key rejected: check ANTHROPIC_API_KEY") from e
        except anthropic.CredentialsError as e:
            raise AIError("No Anthropic credentials: set ANTHROPIC_API_KEY or run `ant auth login`") from e
        except anthropic.NotFoundError as e:
            raise AIError(f"Unknown Claude model '{self.model}'") from e
        except anthropic.RateLimitError as e:
            raise AIError("Anthropic rate limit hit: try again in a moment") from e
        except anthropic.APIStatusError as e:
            raise AIError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AIError("Cannot reach the Anthropic API (offline?)") from e
        if resp.stop_reason == "refusal":
            raise AIError("Claude declined to answer this one.")
        return "".join(b.text for b in resp.content if b.type == "text")

    def _ask_ollama(self, messages: list[dict], on_text) -> str:
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        if not host.startswith("http"):
            host = f"http://{host}"
        if not self.model:
            tags = _http_json(f"{host}/api/tags", None, self.timeout, "Ollama")
            names = [m["name"] for m in tags.get("models", [])]
            if not names:
                raise AIError("Ollama has no models: ollama pull qwen2.5 (or llama3.2)")
            self.model = names[0]
        # Thinking models (qwen3, deepseek-r1...) take minutes per answer with thinking on; a short
        # explanation does not need it. Models without a thinking mode reject the flag: retry without.
        body = {"model": self.model, "stream": True, "think": False,
                "messages": [{"role": "system", "content": SYSTEM}, *messages]}
        try:
            lines = _http_lines(f"{host}/api/chat", body, self.timeout, "Ollama")
        except AIError as e:
            if "think" not in str(e):
                raise
            body.pop("think")
            lines = _http_lines(f"{host}/api/chat", body, self.timeout, "Ollama")
        text = ""
        for line in lines:
            chunk = json.loads(line)
            if chunk.get("error"):
                raise AIError(f"Ollama: {chunk['error']}")
            text += chunk.get("message", {}).get("content", "")
            on_text(text)
            if chunk.get("done"):
                break
        return text

    def _ask_openai(self, messages: list[dict], on_text) -> str:
        base = os.environ.get("SO101CAL_AI_URL", "https://api.openai.com/v1").rstrip("/")
        headers = {}
        if os.environ.get("OPENAI_API_KEY"):
            headers["Authorization"] = f"Bearer {os.environ['OPENAI_API_KEY']}"
        body = {"model": self.model, "messages": [{"role": "system", "content": SYSTEM}, *messages]}
        data = _http_json(f"{base}/chat/completions", body, self.timeout, base, headers)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise AIError(f"Unexpected answer from {base}") from e
        on_text(text)
        return text


def _request(url: str, body: dict | None, headers: dict | None) -> urllib.request.Request:
    return urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                  headers={"Content-Type": "application/json", **(headers or {})})


def _translate(e: Exception, name: str, timeout: float) -> AIError:
    if isinstance(e, urllib.error.HTTPError):
        return AIError(f"{name} answered {e.code}: {e.read().decode(errors='replace')[:200]}")
    if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
        return AIError(f"{name} did not answer within {timeout:.0f} s (a large model loading for the first time "
                       "can be slow; try again or use a smaller model)")
    hint = " (is `ollama serve` running?)" if name == "Ollama" else ""
    return AIError(f"Cannot reach {name}{hint}")


def _http_json(url: str, body: dict | None, timeout: float, name: str, headers: dict | None = None) -> dict:
    try:
        with urllib.request.urlopen(_request(url, body, headers), timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise _translate(e, name, timeout) from e


def _http_lines(url: str, body: dict, timeout: float, name: str):
    """Yield the non-empty lines of a streamed (NDJSON) response."""
    try:
        r = urllib.request.urlopen(_request(url, body, None), timeout=timeout)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise _translate(e, name, timeout) from e

    def gen():
        with r:
            try:
                for line in r:
                    if line.strip():
                        yield line
            except (TimeoutError, ConnectionError) as e:
                raise _translate(e, name, timeout) from e

    return gen()
