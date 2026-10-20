"""
Auto-imported (via PYTHONPATH) into every lab subprocess the provider runs.

1. Transcript recorder (GUARDRAIL_TRANSCRIPT=<path>): appends each chat
   completion's request (messages, tool definitions) and the model's reply as a
   JSON line, so every row carries proof of what the model was shown (the
   poisoned tool description, the injected tool result, the retrieved doc) and
   what it did with it.

2. When a test sets `reasoning_effort`, pins the reasoning budget on every chat completion the lab
makes, without editing the pinned AgenticGoat submodule.

Why: the labs were written for non-reasoning local models and cap answers at
small max_tokens (Lab 04: 500). A reasoning carrier such as gpt-oss-20b can
spend that whole budget thinking and return content=None, which crashes the
driver and reads as "did not run". A constant low effort keeps the carrier
identical across rows (REQ-MD-01) while leaving the app's own prompt and
token budget untouched.
"""

import json
import os

_effort = os.environ.get("GUARDRAIL_REASONING_EFFORT")
if _effort:
    try:
        from openai.resources.chat.completions import Completions

        _orig_create = Completions.create

        def _create(self, *args, **kwargs):
            extra = dict(kwargs.pop("extra_body", None) or {})
            extra.setdefault("reasoning", {"effort": _effort})
            return _orig_create(self, *args, extra_body=extra, **kwargs)

        Completions.create = _create
    except Exception:  # noqa: BLE001 - never break the lab over the shim
        pass


_transcript = os.environ.get("GUARDRAIL_TRANSCRIPT")
if _transcript:
    try:
        from openai.resources.chat.completions import Completions

        _inner_create = Completions.create

        def _plain(obj):
            dump = getattr(obj, "model_dump", None)
            return dump(exclude_none=True) if callable(dump) else str(obj)

        def _recorded(self, *args, **kwargs):
            response = _inner_create(self, *args, **kwargs)
            try:
                choices = getattr(response, "choices", None) or []
                entry = {
                    "messages": kwargs.get("messages"),
                    "tools": kwargs.get("tools"),
                    "reply": choices[0].message if choices else None,
                }
                with open(_transcript, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, default=_plain) + "\n")
            except Exception:  # noqa: BLE001 - recording must never break the lab
                pass
            return response

        Completions.create = _recorded
    except Exception:  # noqa: BLE001
        pass
