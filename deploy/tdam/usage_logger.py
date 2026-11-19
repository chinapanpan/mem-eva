"""LiteLLM proxy callback: append token usage of every TDAM-internal LLM/embedding call to a JSONL file,
so the benchmark can attribute TDAM's extraction cost (TDAM itself does not expose it)."""
import json
import os
import time

from litellm.integrations.custom_logger import CustomLogger

PATH = os.environ.get("MEMEVA_LITELLM_USAGE", "/var/log/memeva/litellm_usage.jsonl")


class UsageLogger(CustomLogger):
    def _write(self, kwargs, response_obj, start_time, end_time):
        try:
            usage = getattr(response_obj, "usage", None) or {}
            get = usage.get if isinstance(usage, dict) else lambda k, d=None: getattr(usage, k, d)
            row = {"ts": time.time(), "model": kwargs.get("model"), "call_type": kwargs.get("call_type"),
                   "prompt_tokens": get("prompt_tokens", 0), "completion_tokens": get("completion_tokens", 0),
                   "seconds": (end_time - start_time).total_seconds()}
            os.makedirs(os.path.dirname(PATH), exist_ok=True)
            with open(PATH, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception:  # never break the proxy
            pass

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._write(kwargs, response_obj, start_time, end_time)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._write(kwargs, response_obj, start_time, end_time)


proxy_handler_instance = UsageLogger()
