"""
OpenRouter API client for Hybrid Hunter.
Zero Hermes dependencies. Handles auth, timeouts, rate-limit retry.
"""

import os
import time
import json
from typing import Dict, Any, List, Optional, Tuple
from openai import OpenAI, APIStatusError, APITimeoutError, APIConnectionError


class ORClient:
    """Thin wrapper around the OpenAI SDK pointed at OpenRouter."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 timeout: int = 45, max_retries: int = 3, retry_delay: float = 2.0):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.base_url = base_url or self.BASE_URL
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client = None

    @property
    def client(self):
        """Lazy-initialize the OpenAI client."""
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    def query(self, model: str, messages: List[Dict[str, str]],
              extra_params: Optional[Dict[str, Any]] = None,
              dry_run: bool = False) -> Dict[str, Any]:
        """
        Send a single query to the model.

        Returns dict with keys:
            content (str): model response text
            latency (float): round-trip seconds
            error (str|None): error message if failed
            model (str): model id used
            usage (dict|None): token usage if available
        """
        if dry_run:
            return {
                "content": f"[DRY RUN] Would send {len(messages)} messages to {model}",
                "latency": 0.0,
                "error": None,
                "model": model,
                "usage": None,
            }

        params = {}
        if extra_params:
            # Keep local metadata out of the OpenAI/OpenRouter API request.
            params.update({k: v for k, v in extra_params.items() if not k.startswith("_")})

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                start = time.time()
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **params,
                )
                latency = time.time() - start
                if not resp.choices:
                    return {
                        "content": "",
                        "latency": round(latency, 3),
                        "error": "no_choices_returned: API returned no completion choices (filtered/moderation)",
                        "model": model,
                        "usage": None,
                    }
                content = resp.choices[0].message.content or ""
                usage = None
                if resp.usage:
                    usage = {
                        "prompt_tokens": resp.usage.prompt_tokens,
                        "completion_tokens": resp.usage.completion_tokens,
                        "total_tokens": resp.usage.total_tokens,
                    }
                return {
                    "content": content,
                    "latency": round(latency, 3),
                    "error": None,
                    "model": model,
                    "usage": usage,
                }
            except APITimeoutError as e:
                last_error = f"timeout (attempt {attempt}/{self.max_retries})"
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * attempt)
            except APIStatusError as e:
                if e.status_code == 429:
                    last_error = f"rate_limited (attempt {attempt}/{self.max_retries})"
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay * attempt * 2)  # longer backoff for 429
                else:
                    return {
                        "content": "",
                        "latency": 0.0,
                        "error": f"http_{e.status_code}: {e.message}",
                        "model": model,
                        "usage": None,
                    }
            except APIConnectionError as e:
                last_error = f"connection_error (attempt {attempt}/{self.max_retries})"
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * attempt)
            except Exception as e:
                return {
                    "content": "",
                    "latency": 0.0,
                    "error": f"unexpected: {type(e).__name__}: {e}",
                    "model": model,
                    "usage": None,
                }

        return {
            "content": "",
            "latency": 0.0,
            "error": last_error or "max_retries_exceeded",
            "model": model,
            "usage": None,
        }
