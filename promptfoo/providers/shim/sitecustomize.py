"""
Auto-imported (via PYTHONPATH) into a lab subprocess when a test sets
`reasoning_effort`. Pins the reasoning budget on every chat completion the lab
makes, without editing the pinned AgenticGoat submodule.

Why: the labs were written for non-reasoning local models and cap answers at
small max_tokens (Lab 04: 500). A reasoning carrier such as gpt-oss-20b can
spend that whole budget thinking and return content=None, which crashes the
driver and reads as "did not run". A constant low effort keeps the carrier
identical across rows (REQ-MD-01) while leaving the app's own prompt and
token budget untouched.
"""

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
