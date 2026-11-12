"""mini-swe's OpenRouter chat model, adjusted for long-thinking models on the host runner.

Differences from minisweagent.models.openrouter_model.OpenRouterModel:
- The HTTP timeout comes from model_kwargs["timeout"] (default 3600 s) instead of a fixed
  60 s; a client-side timeout would retry a call OpenRouter may still bill.
- Connection values the host runner passes to query() (api_key, api_base, extra_headers)
  are used for the request itself and never sent in the JSON body.
- Every request of one agent run carries the same `session_id`, so OpenRouter keeps the
  run on one provider and its prompt cache stays warm.
Everything else (tool calling, cost from usage.cost, retries, saving the full returned
message including `reasoning` and `reasoning_details`) is the stock behaviour.
"""
import json
import uuid

import requests

from minisweagent.models.openrouter_model import (
    BASH_TOOL,
    OpenRouterAPIError,
    OpenRouterAuthenticationError,
    OpenRouterModel,
    OpenRouterRateLimitError,
)


class LongThinkingOpenRouterModel(OpenRouterModel):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._session_id = uuid.uuid4().hex

    def _query(self, messages, **kwargs):
        options = self.config.model_kwargs | kwargs
        api_key = options.pop("api_key", None) or self._api_key
        api_base = options.pop("api_base", None) or "https://openrouter.ai/api/v1"
        timeout = options.pop("timeout", 3600)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                   **options.pop("extra_headers", {})}
        payload = {"model": self.config.model_name, "messages": messages, "tools": [BASH_TOOL],
                   "session_id": self._session_id, **options}
        try:
            response = requests.post(api_base.rstrip("/") + "/chat/completions", headers=headers,
                                     data=json.dumps(payload), timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                raise OpenRouterAuthenticationError("OpenRouter authentication failed") from e
            if response.status_code == 429:
                raise OpenRouterRateLimitError("Rate limit exceeded") from e
            raise OpenRouterAPIError(f"HTTP {response.status_code}: {response.text}") from e
        except requests.exceptions.RequestException as e:
            raise OpenRouterAPIError(f"Request failed: {e}") from e
