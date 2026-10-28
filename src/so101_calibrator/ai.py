"""Optional assistant: explains the current step or a failed check, and answers questions.

Off unless configured. A spec is `provider[:model]`:

    ollama[:model]        local Ollama ($OLLAMA_HOST, default http://localhost:11434)
    openai:model          any OpenAI-compatible server: $SO101CAL_AI_URL (LM Studio http://localhost:1234/v1,
                          llama.cpp, vLLM) or api.openai.com with $OPENAI_API_KEY
    claude[:model]        Anthropic API, key from ANTHROPIC_API_KEY or `ant auth login`

Local and OpenAI-compatible models run as a Strands agent (strands-agents) with read-only tools over the
calibration history; Claude runs on the Anthropic SDK. Both come with `pip install "so101-calibrator[ai]"`.
Set the model with `so101-cal ai use <spec>`, `--ai <spec>` or $SO101CAL_AI. The assistant only sees what the
tool shows (steps, check results, joint readings, stored versions); it has no tool that moves the arm.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import i18n, settings
from .profiles import JOINTS
from .store import Store, StoreError

PROVIDERS = ("ollama", "openai", "claude")
CLAUDE_MODEL = "claude-opus-5-5"
INSTALL_HINT = 'pip install "so101-calibrator[ai]"'
LANG_NAMES = {"en": "English", "tr": "Turkish"}

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
- Use only the numbers in the state or from your tools; do not invent register values or readings.
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
def load_spec(store_root, override: str | None = None) -> str | None:
    """--ai flag, then $SO101CAL_AI, then `so101-cal ai use ...`; "off" disables."""
    if override:
        return None if override == "off" else override
    if os.environ.get("SO101CAL_AI"):
        return os.environ["SO101CAL_AI"]
    return settings.load(store_root).get("ai")


def save_spec(store_root, spec: str | None) -> None:
    settings.update(store_root, ai=spec)


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


# --- read-only tools for the agent -------------------------------------------------------------------
def history_tools(store: Store) -> list:
    """Strands tools over the calibration history. Read-only on purpose: nothing here touches hardware."""
    from strands import tool

    @tool
    def calibration_history(arm_id: str) -> str:
        """All stored calibration versions of an arm ("follower", "leader" or another id): version, date,
        source, note, active flag and how many checks passed, warned or failed."""
        try:
            info = store.arm(arm_id)
            if info is None:
                known = ", ".join(a["arm_id"] for a in store.arms()) or "none"
                return f"error: no calibrations stored for '{arm_id}' (known arms: {known})"
            return json.dumps([{**r.summary(), "active": r.version == info.get("active")}
                               for r in store.versions(arm_id)])
        except StoreError as e:
            return f"error: {e}"

    @tool
    def compare_versions(arm_id: str, a: int, b: int) -> str:
        """Per-joint differences (homing offset, range min/max, in encoder ticks) between two stored versions."""
        try:
            return json.dumps(store.diff(arm_id, a, b))
        except StoreError as e:
            return f"error: {e}"

    return [calibration_history, compare_versions]


# --- the assistant -------------------------------------------------------------------------------------
@dataclass
class Assistant:
    spec: str
    store: Store | None = None  # gives local models the history tools
    timeout: float = 120.0
    history: list[dict] = field(default_factory=list)  # Claude conversation (Strands keeps its own)

    def __post_init__(self):
        self.provider, self.model = parse_spec(self.spec)
        if self.provider == "claude" and not self.model:
            self.model = CLAUDE_MODEL
        self._agent: Any = None
        self._on_text: Callable[[str], None] = lambda _: None
        self._text = ""

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider

    def ask(self, question: str, state: dict, on_text: Callable[[str], None] | None = None,
            lang: str | None = None) -> str:
        """Answer `question` about `state`. `on_text` gets the answer so far while it streams in."""
        language = LANG_NAMES.get(lang or i18n.get_lang(), "English")
        prompt = (f"State:\n{json.dumps(state, separators=(',', ':'))}\n\n"
                  f"Question: {question.strip() or f'(none: explain the current problem, in {language})'}")
        on_text = on_text or (lambda _: None)
        if self.provider == "claude":
            return self._ask_claude(question, prompt, on_text).strip()
        return self._ask_strands(prompt, on_text).strip()

    # --- Strands: Ollama and OpenAI-compatible servers -------------------------------------------------
    def _model(self):
        if self.provider == "ollama":
            from strands.models.ollama import OllamaModel

            host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
            host = host if host.startswith("http") else f"http://{host}"
            if not self.model:
                self.model = self._first_ollama_model(host)
            # thinking models (qwen3, deepseek-r1) take minutes per answer with thinking on
            return OllamaModel(host, model_id=self.model, keep_alive="10m", additional_args={"think": False},
                               ollama_client_args={"timeout": self.timeout})
        from strands.models.openai import OpenAIModel

        return OpenAIModel(client_args={"base_url": os.environ.get("SO101CAL_AI_URL", "https://api.openai.com/v1"),
                                        "api_key": os.environ.get("OPENAI_API_KEY") or "not-needed",
                                        "timeout": self.timeout},
                           model_id=self.model)

    def _first_ollama_model(self, host: str) -> str:
        import ollama

        try:
            names = [m.model for m in ollama.Client(host, timeout=10).list().models]
        except Exception as e:
            raise AIError(f"Cannot reach Ollama at {host} (is `ollama serve` running?)") from e
        if not names:
            raise AIError("Ollama has no models: ollama pull qwen2.5 (or llama3.2)")
        return names[0]

    def _make_agent(self, with_tools: bool):
        from strands import Agent

        def stream(**event):  # Strands callback: text chunks arrive as "data"
            if "data" in event:
                self._text += event["data"]
                self._on_text(self._text)

        tools = history_tools(self.store) if with_tools and self.store is not None else []
        return Agent(model=self._model(), system_prompt=SYSTEM, tools=tools, callback_handler=stream)

    def _ask_strands(self, prompt: str, on_text: Callable[[str], None]) -> str:
        try:
            import strands  # noqa: F401
        except ImportError as e:
            raise AIError(f"Local and OpenAI-compatible models need Strands Agents: {INSTALL_HINT}") from e
        self._on_text, self._text = on_text, ""
        try:
            if self._agent is None:
                self._agent = self._make_agent(with_tools=True)
            try:
                result = self._agent(prompt)
            except Exception as e:
                if "does not support tools" not in str(e):
                    raise
                self._agent = self._make_agent(with_tools=False)  # small models: answer from the state alone
                self._text = ""
                result = self._agent(prompt)
        except AIError:
            raise
        except Exception as e:
            raise AIError(_explain(e, self.provider)) from e
        return self._text or str(result)

    # --- Claude: Anthropic SDK -------------------------------------------------------------------------
    def _ask_claude(self, question: str, prompt: str, on_text: Callable[[str], None]) -> str:
        try:
            import anthropic
        except ImportError as e:
            raise AIError(f"Claude needs the Anthropic SDK: {INSTALL_HINT}") from e
        messages = [*self.history[-6:], {"role": "user", "content": prompt}]
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
        answer = "".join(b.text for b in resp.content if b.type == "text")
        # keep only the question text in history; the next turn carries the fresh state
        self.history += [{"role": "user", "content": question.strip() or "Explain the current problem."},
                         {"role": "assistant", "content": answer}]
        return answer


def _explain(e: Exception, provider: str) -> str:
    text = f"{type(e).__name__}: {e}"
    low = text.lower()
    if provider == "ollama" and ("connect" in low or "refused" in low):
        return "Cannot reach Ollama (is `ollama serve` running?)"
    if "not found" in low and provider == "ollama":
        return f"{text} (ollama pull <model>?)"
    if "timeout" in low or "timed out" in low:
        return "The model did not answer in time (a large model loading for the first time can be slow)"
    return text
