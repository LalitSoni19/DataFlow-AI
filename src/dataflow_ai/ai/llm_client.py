"""LLM client adapters for OpenAI-compatible and Ollama endpoints."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dataflow_ai.utils import sanitize_secret_string, sanitize_uri

LOGGER = logging.getLogger("dataflow_ai.ai.llm_client")


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMClient:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = 20.0
    max_retries: int = 2
    fallback_models: tuple[str, ...] = ()

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        models_to_try = [self.model]
        for fallback in self.fallback_models:
            if fallback and fallback not in models_to_try:
                models_to_try.append(fallback)

        last_error: Exception | None = None
        for idx, candidate_model in enumerate(models_to_try):
            payload = {
                "model": candidate_model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            try:
                response = _post_json(
                    endpoint,
                    payload,
                    self.api_key,
                    timeout=self.timeout_seconds,
                    max_retries=self.max_retries,
                )
                return _message_content(response["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as error:
                last_error = LLMError("The OpenAI-compatible provider returned an unexpected response.")
                if idx < len(models_to_try) - 1:
                    LOGGER.warning(
                        "LLM model '%s' response parsing failed (%s); trying fallback '%s'.",
                        candidate_model,
                        error,
                        models_to_try[idx + 1],
                    )
                    continue
                raise last_error from error
            except (LLMError, HTTPError, TimeoutError) as error:
                last_error = error if isinstance(error, LLMError) else LLMError(f"LLM request failed: {error}")
                if idx < len(models_to_try) - 1:
                    LOGGER.warning(
                        "LLM model '%s' request failed (%s); trying fallback '%s'.",
                        candidate_model,
                        error,
                        models_to_try[idx + 1],
                    )
                    continue
                if isinstance(error, LLMError):
                    raise
                raise last_error from error

        if last_error:
            raise last_error
        raise LLMError("No model available to handle completion.")


@dataclass(frozen=True)
class OllamaClient:
    base_url: str
    model: str
    timeout_seconds: float = 20.0
    max_retries: int = 1

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        endpoint = self.base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = _post_json(
            endpoint,
            payload,
            None,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
        )
        try:
            return _message_content(response["message"]["content"])
        except (KeyError, TypeError) as error:
            raise LLMError("Ollama returned an unexpected response.") from error


def _message_content(content: Any) -> str:
    """Normalize both classic string and structured OpenAI-compatible output."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type", "text") in {"text", "output_text"}
        ]
        if text_parts:
            return "".join(text_parts)
    if content is None:
        return ""
    raise TypeError("message content was not text")


def _post_json(
    url: str,
    payload: dict[str, Any],
    api_key: str | None,
    *,
    timeout: float = 20.0,
    max_retries: int = 2,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    max_attempts = max(1, max_retries + 1)
    for attempt in range(max_attempts):
        request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                if isinstance(result, dict):
                    return result
                raise LLMError(f"LLM request failed ({sanitize_uri(url)}): Expected JSON object response.")
        except HTTPError as error:
            if error.code in (429, 502, 503, 504) and attempt < max_attempts - 1:
                sleep_sec = min(3.0, 0.8 * (2 ** attempt) + 0.1 * attempt)
                time.sleep(sleep_sec)
                continue
            clean_url = sanitize_uri(url)
            clean_error = sanitize_secret_string(str(error))
            raise LLMError(f"LLM request failed ({clean_url}): {clean_error}") from error
        except (URLError, json.JSONDecodeError, TimeoutError) as error:
            if attempt < max_attempts - 1:
                sleep_sec = min(3.0, 0.8 * (2 ** attempt) + 0.1 * attempt)
                time.sleep(sleep_sec)
                continue
            clean_url = sanitize_uri(url)
            clean_error = sanitize_secret_string(str(error))
            raise LLMError(f"LLM request failed ({clean_url}): {clean_error}") from error

    raise LLMError(f"LLM request failed ({sanitize_uri(url)}): Maximum retry attempts reached.")


