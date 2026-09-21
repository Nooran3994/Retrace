"""Local model provider layer for Retrace.

Integrity contract:
  - The capture/detect loop NEVER depends on a model. If no provider is
    configured or reachable, Retrace runs fully deterministic.
  - Model analysis is OPT-IN via `retrace config set model.provider`.
  - Only REDACTED text is ever sent to a model. Redaction happens in
    redact.py BEFORE this module sees the data.
  - Model output is treated as advisory: it may INSERT insight rows into
    the alerts table, but can never modify or delete command rows.

Providers:
  - null      : no model. Default. Everything still works.
  - ollama    : local Ollama server (http://127.0.0.1:11434). Fully local.
  - openai    : any OpenAI-compatible endpoint (LM Studio, llama.cpp
                server, vLLM, etc.). URL configurable. Still local if you
                point it at 127.0.0.1.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Config (~/.config/retrace/config.json)
# ---------------------------------------------------------------------------


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "retrace" / "config.json"


DEFAULTS = {
    "model": {
        "provider": "null",          # null | ollama | openai
        "url": "http://127.0.0.1:11434",
        "model": "llama3.2:3b",      # ollama model tag
        "timeout_s": 60,
        "enabled": False,            # master switch; stays False until user opts in
        "max_input_chars": 12000,    # cap on text sent per analysis call
        "system_prompt": (
            "You are a security analyst reviewing terminal session data. "
            "Identify suspicious commands, risky patterns, or anomalies. "
            "Reply with JSON: {\"insights\":[{\"severity\":\"low|medium|high\","
            "\"title\":\"...\",\"detail\":\"...\"}]}. Be concise. "
            "If nothing is suspicious, return {\"insights\":[]}."
        ),
    }
}


def load_config() -> dict:
    p = config_path()
    if not p.exists():
        return json.loads(json.dumps(DEFAULTS))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULTS))
    # shallow merge so new keys appear even if the file is old
    merged = json.loads(json.dumps(DEFAULTS))
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
    return merged


def save_config(cfg: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


def set_config(key: str, value: str) -> dict:
    """Set a dotted config key (e.g. model.provider=ollama). Returns cfg."""
    cfg = load_config()
    parts = key.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    raw: object = value
    # best-effort type coercion
    if value.lower() in ("true", "false"):
        raw = value.lower() == "true"
    else:
        try:
            raw = int(value)
        except ValueError:
            try:
                raw = float(value)
            except ValueError:
                raw = value
    node[parts[-1]] = raw
    save_config(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class ModelProvider:
    """Base class. Subclasses implement analyze()."""

    name = "base"

    def available(self) -> bool:
        return False

    def analyze(self, text: str, system_prompt: str | None = None) -> list[dict]:
        """Return a list of insight dicts. Never raises — returns [] on error."""
        return []


class NullProvider(ModelProvider):
    """Default. No model. Deterministic only."""

    name = "null"

    def available(self) -> bool:
        return False


class OllamaProvider(ModelProvider):
    """Local Ollama server. Fully offline, no API keys."""

    name = "ollama"

    def __init__(self, url: str = "http://127.0.0.1:11434", model: str = "llama3.2:3b",
                 timeout_s: int = 60):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    def available(self) -> bool:
        try:
            req = urllib.request.Request(
                f"{self.url}/api/tags", method="GET", headers={"Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                return isinstance(data, list) and len(data) > 0
        except Exception:
            return False

    def analyze(self, text: str, system_prompt: str | None = None) -> list[dict]:
        prompt = system_prompt or (
            "You are a security analyst reviewing terminal session data. "
            "Identify suspicious commands, risky patterns, or anomalies. "
            "Reply with JSON: {\"insights\":[{\"severity\":\"low|medium|high\","
            "\"title\":\"...\",\"detail\":\"...\"}]}. Be concise. "
            "If nothing is suspicious, return {\"insights\":[]}."
        )
        payload = json.dumps({
            "model": self.model,
            "prompt": f"{prompt}\n\nSESSION DATA:\n{text}\n\nJSON:",
            "stream": False,
            "options": {"temperature": 0.1},
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{self.url}/api/generate",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            return []

        raw = body.get("response", "")
        return _parse_json_insights(raw)


class OpenAICompatProvider(ModelProvider):
    """Any OpenAI-compatible /chat/completions endpoint.

    Point url at a local server (LM Studio, llama.cpp, vLLM) for full
    offline operation, or at a remote endpoint if the user explicitly
    accepts that tradeoff. Default stays local.
    """

    name = "openai"

    def __init__(self, url: str = "http://127.0.0.1:1234/v1", model: str = "local-model",
                 timeout_s: int = 60, api_key: str = ""):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.api_key = api_key

    def available(self) -> bool:
        try:
            req = urllib.request.Request(
                f"{self.url}/models",
                method="GET",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def analyze(self, text: str, system_prompt: str | None = None) -> list[dict]:
        prompt = system_prompt or (
            "You are a security analyst reviewing terminal session data. "
            "Identify suspicious commands, risky patterns, or anomalies. "
            "Reply with JSON: {\"insights\":[{\"severity\":\"low|medium|high\","
            "\"title\":\"...\",\"detail\":\"...\"}]}. Be concise. "
            "If nothing is suspicious, return {\"insights\":[]}."
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.1,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{self.url}/chat/completions",
                data=payload,
                method="POST",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            return []

        try:
            raw = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return []
        return _parse_json_insights(raw)


def _parse_json_insights(raw: str) -> list[dict]:
    """Best-effort parse of model JSON output. Never raises."""
    if not raw:
        return []
    # strip markdown fences if the model wrapped the JSON
    clean = raw.strip()
    if clean.startswith("```"):
        clean = clean.strip("`")
        if clean.startswith("json"):
            clean = clean[4:]
        clean = clean.strip()
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        # try to find the first {...} block
        start = clean.find("{")
        end = clean.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            data = json.loads(clean[start:end + 1])
        except json.JSONDecodeError:
            return []
    if not isinstance(data, dict):
        return []
    insights = data.get("insights", [])
    if not isinstance(insights, list):
        return []
    out = []
    for item in insights:
        if not isinstance(item, dict):
            continue
        sev = str(item.get("severity", "low")).lower()
        if sev not in ("low", "medium", "high"):
            sev = "low"
        out.append({
            "severity": sev,
            "title": str(item.get("title", "Model insight"))[:200],
            "detail": str(item.get("detail", ""))[:2000],
        })
    return out


def get_provider(cfg: dict | None = None) -> ModelProvider:
    """Instantiate the configured provider. Falls back to null on error."""
    cfg = cfg or load_config()
    m = cfg.get("model", {})
    provider = str(m.get("provider", "null")).lower()
    url = str(m.get("url", DEFAULTS["model"]["url"]))
    model = str(m.get("model", DEFAULTS["model"]["model"]))
    timeout_s = int(m.get("timeout_s", DEFAULTS["model"]["timeout_s"]))
    if provider == "ollama":
        return OllamaProvider(url=url, model=model, timeout_s=timeout_s)
    if provider == "openai":
        return OpenAICompatProvider(url=url, model=model, timeout_s=timeout_s)
    return NullProvider()


def analyze_recent(conn, since_minutes: int = 60, limit: int = 200,
                   cfg: dict | None = None) -> list[dict]:
    """Fetch recent REDACTED commands, run model analysis, return insights.

    Integrity: reads only the `command` column (already redacted), never
    raw_output, and never writes here — the caller decides what to do
    with the insights.
    """
    cfg = cfg or load_config()
    m = cfg.get("model", {})
    if not m.get("enabled", False):
        return []
    provider = get_provider(cfg)
    if not provider.available():
        return []

    since = time.time() - since_minutes * 60
    rows = conn.execute(
        "SELECT command FROM commands WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    if not rows:
        return []

    # Build a compact redacted transcript. Commands are already redacted
    # at ingest, but we re-run redact_line as defense in depth.
    from .redact import redact_line
    text_parts = []
    for (cmd,) in rows:
        safe = redact_line(cmd or "")
        if safe.strip():
            text_parts.append(safe)
    transcript = "\n".join(text_parts)
    max_chars = int(m.get("max_input_chars", DEFAULTS["model"]["max_input_chars"]))
    if len(transcript) > max_chars:
        transcript = transcript[-max_chars:]

    return provider.analyze(transcript, system_prompt=m.get("system_prompt"))