"""Ollama chat/generate provider for LLM-assisted analysis tools.

Used by ``summarise_module`` (and future tools) to produce natural-language
descriptions of code modules.  Requires a running ``ollama serve`` and at
least one chat-capable model (e.g. ``qwen2.5-coder:1.5b``).

Configuration via ``.vc-context/conventions.json``:

    {
      "chat_provider": {
        "name": "ollama",
        "model": "qwen2.5-coder:1.5b",
        "host": "http://localhost:11434"
      }
    }

String shorthand also works:  ``"chat_provider": "ollama"``  (uses defaults).

If ``chat_provider`` is absent the module is not used — all callers degrade
gracefully.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional


class OllamaChatProvider:
    DEFAULT_MODEL = "qwen2.5-coder:1.5b"
    DEFAULT_HOST = "http://localhost:11434"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        timeout: int = 60,
    ) -> str:
        """Send a non-streaming generate request; return the response text.

        Raises ``RuntimeError`` when the server is unreachable or the
        model is not installed — callers must catch this and fall back.
        """
        payload: dict = {"model": self.model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return str(body.get("response", "")).strip()
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"ollama serve is not running at {self.host} — start it with: ollama serve\n({exc})"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Ollama generate failed for model '{self.model}': {exc}") from exc


def chat_provider_from_conventions(project_root: str) -> Optional[OllamaChatProvider]:
    """Return a chat provider from ``.vc-context/conventions.json``, or
    ``None`` when not configured.  Never raises.
    """
    conv_path = os.path.join(project_root, ".vc-context", "conventions.json")
    if not os.path.isfile(conv_path):
        return None
    try:
        with open(conv_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    chat_cfg = cfg.get("chat_provider")
    if not chat_cfg:
        return None

    if isinstance(chat_cfg, str):
        return OllamaChatProvider() if chat_cfg == "ollama" else None

    if isinstance(chat_cfg, dict) and chat_cfg.get("name") == "ollama":
        return OllamaChatProvider(
            model=chat_cfg.get("model", OllamaChatProvider.DEFAULT_MODEL),
            host=chat_cfg.get("host", OllamaChatProvider.DEFAULT_HOST),
        )

    return None
