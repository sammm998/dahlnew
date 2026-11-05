"""Tunn klient mot Claude (Anthropic API) med strukturerade svar.

AI används bara för FÖRSLAG. Inget svar tillämpas automatiskt: varje förslag
sparas, visas för en behörig granskare och valideras av den deterministiska
regelmotorn innan något ändras.

Konfiguration (miljövariabler):
    ANTHROPIC_API_KEY            API-nyckel (krävs för att AI-funktionerna ska vara på)
    SMARTHANGERS_AI_MODEL        modell, standard claude-opus-5-5
    SMARTHANGERS_AI_FALLBACKS    "1" (standard) = serverside fallback vid policyavslag, "0" = av
    SMARTHANGERS_AI_DISABLED     "1" stänger av AI helt
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_MODEL = "claude-opus-5-5"

_override_client = None  # för tester


class AIUnavailable(Exception):
    """AI är inte konfigurerad (ingen nyckel) eller avstängd."""


class AIError(Exception):
    """Anropet misslyckades eller gav inget användbart svar."""


@dataclass
class AIResult:
    data: dict
    model: str
    request_id: Optional[str] = None
    usage: dict = field(default_factory=dict)
    stop_reason: Optional[str] = None


def model_name() -> str:
    return os.environ.get("SMARTHANGERS_AI_MODEL", DEFAULT_MODEL)


def is_enabled() -> bool:
    if _override_client is not None:
        return True
    if os.environ.get("SMARTHANGERS_AI_DISABLED") == "1":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def set_client(client) -> None:
    """Injicera en klient (används av tester). None återställer."""
    global _override_client
    _override_client = client


def _client():
    if _override_client is not None:
        return _override_client
    if not is_enabled():
        raise AIUnavailable("AI är inte konfigurerad. Sätt ANTHROPIC_API_KEY på servern för att aktivera AI-förslag.")
    import anthropic
    return anthropic.Anthropic(max_retries=3, timeout=300.0)


def structured(system: str, content: list[dict], schema: dict, *, effort: str = "medium",
               max_tokens: int = 16000) -> AIResult:
    """Ett anrop som returnerar JSON enligt `schema` (strukturerade svar)."""
    import anthropic

    client = _client()
    kwargs: dict[str, Any] = dict(
        model=model_name(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    try:
        if os.environ.get("SMARTHANGERS_AI_FALLBACKS", "1") == "1":
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as e:
        raise AIUnavailable("Ogiltig API-nyckel för AI.") from e
    except anthropic.RateLimitError as e:
        raise AIError("AI-tjänsten är tillfälligt överbelastad (rate limit). Försök igen om en stund.") from e
    except anthropic.BadRequestError as e:
        raise AIError(f"AI-anropet avvisades: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"AI-tjänsten svarade med fel {e.status_code}.") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Kunde inte nå AI-tjänsten.") from e

    if resp.stop_reason == "refusal":
        raise AIError("AI-modellen avböjde att svara på förfrågan.")
    if resp.stop_reason == "max_tokens":
        raise AIError("AI-svaret blev för långt och avbröts. Dela upp underlaget och försök igen.")
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
    if not text:
        raise AIError("AI-svaret saknade innehåll.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError("AI-svaret var inte giltig JSON.") from e
    usage = {}
    u = getattr(resp, "usage", None)
    if u is not None:
        usage = {"input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None)}
    return AIResult(data=data, model=getattr(resp, "model", model_name()), request_id=getattr(resp, "_request_id", None),
                    usage=usage, stop_reason=resp.stop_reason)


def nullable(t: str) -> dict:
    return {"type": [t, "null"]}
