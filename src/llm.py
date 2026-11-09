"""The ONLY place that talks to a language model (rule 8).

Uses the `openai` package against any OpenAI-compatible endpoint, so switching
provider (Gemini free tier -> Groq -> Ollama -> a paid API) is a .env change only.

.env keys:
    LLM_PROVIDER   gemini | groq | ollama | openai | custom
    LLM_MODEL      e.g. gemini-2.5-flash, llama-3.3-70b-versatile, llama3.1
    LLM_API_KEY    not needed for ollama
    LLM_BASE_URL   optional; overrides the provider default
    LLM_REASONING_EFFORT  optional, e.g. "none" / "low" for thinking models

Quick check:  python -m src.llm            (sends one test prompt)
List models:  python -m src.llm --models
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Protocol, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
    "openai": "https://api.openai.com/v1",
}
DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "ollama": "llama3.1",
    "openai": "gpt-4o-mini",
}


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    """Anything with these two methods can be used by extract.py / drafting.py (e.g. a fake in tests)."""

    def complete(self, system: str, prompt: str, temperature: float = 0.3) -> str: ...

    def extract(self, system: str, prompt: str, schema: type[T]) -> T: ...


@dataclass
class LLMSettings:
    provider: str
    model: str
    base_url: str
    api_key: str
    reasoning_effort: str | None = None

    @classmethod
    def from_env(cls) -> "LLMSettings":
        load_dotenv()
        provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
        base_url = os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URLS.get(provider)
        if not base_url:
            raise LLMError(f"No LLM_BASE_URL set and no default for provider '{provider}'.")
        api_key = os.getenv("LLM_API_KEY", "")
        if not api_key:
            if provider == "ollama":
                api_key = "ollama"  # Ollama ignores the key but the client needs one
            else:
                raise LLMError("LLM_API_KEY is empty. Copy .env.example to .env and add your key.")
        return cls(
            provider=provider,
            model=os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(provider, ""),
            base_url=base_url,
            api_key=api_key,
            reasoning_effort=os.getenv("LLM_REASONING_EFFORT") or None,
        )


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_json_text(text: str) -> dict:
    """Parse model output as JSON, tolerating ```json fences or text around the object."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


class OpenAICompatibleLLM:
    """Real client. Created lazily so tests and imports never need an API key."""

    def __init__(self, settings: LLMSettings | None = None):
        from openai import OpenAI  # imported here so tests don't need network setup

        self.settings = settings or LLMSettings.from_env()
        self._client = OpenAI(api_key=self.settings.api_key, base_url=self.settings.base_url)

    def _chat(self, messages: list[dict], temperature: float, json_mode: bool) -> str:
        kwargs: dict = {"model": self.settings.model, "messages": messages, "temperature": temperature}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self.settings.reasoning_effort:
            kwargs["reasoning_effort"] = self.settings.reasoning_effort
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:  # network, auth, rate limit... surface one clear error
            raise LLMError(f"{self.settings.provider} call failed: {e}") from e
        content = resp.choices[0].message.content
        if not content:
            raise LLMError("Empty response from the model.")
        return content

    def complete(self, system: str, prompt: str, temperature: float = 0.3) -> str:
        return self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            temperature, json_mode=False,
        )

    def extract(self, system: str, prompt: str, schema: type[T]) -> T:
        """Ask for JSON matching `schema`; validate with Pydantic; retry once with the error."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        last_error = ""
        for attempt in range(2):
            text = self._chat(messages, temperature=0.0, json_mode=True)
            try:
                return schema.model_validate(parse_json_text(text))
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = str(e)
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": f"That JSON was invalid:\n{last_error}\n"
                                                "Return ONLY the corrected JSON object."},
                ]
        raise LLMError(f"Model returned invalid JSON twice: {last_error}")


def get_llm() -> LLMClient:
    return OpenAICompatibleLLM()


if __name__ == "__main__":
    llm = OpenAICompatibleLLM()
    s = llm.settings
    print(f"Provider: {s.provider} | model: {s.model} | url: {s.base_url}")
    if "--models" in sys.argv:
        for m in llm._client.models.list():
            print(" ", m.id)
    else:
        print(llm.complete("You are terse.", "Reply with exactly: LLM connection OK"))
