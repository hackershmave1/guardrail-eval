"""
Auto-imported (via PYTHONPATH) into every lab subprocess the provider runs.

1. Transcript recorder (GUARDRAIL_TRANSCRIPT=<path>): appends each chat
   completion's request (messages, tool definitions) and the model's reply as a
   JSON line, so every row carries proof of what the model was shown (the
   poisoned tool description, the injected tool result, the retrieved doc) and
   what it did with it. The request is recorded as the lab sent it: the pre-image
   the delivery check reads (outcome.py). Each line also carries finish_reason,
   usage, the trial id and the resample index.

   Empty turns (no text, no tool call) are resampled in place up to
   GUARDRAIL_RESAMPLE times, each one recorded: a dropped turn is a carrier
   glitch, not a verdict. `content_filter` and `length` endings are not
   resampled (the first is NOT-RUN, the second means the token budget is wrong).
   GUARDRAIL_TRIAL_ID is sent as X-Trial-Id so a gateway's log joins the trial.

   Layer order, outermost first: recorder → [vendor hook slot, phase B] →
   effort → pin → route → headers → the real call. A hook goes inside the recorder
   so the recorded request stays the pre-image the hook received.

2. When a test sets `reasoning_effort`, pins the reasoning budget on every chat completion the lab
makes, without editing the pinned AgenticGoat submodule.

Why: the labs were written for non-reasoning local models and cap answers at
small max_tokens (Lab 04: 500). A reasoning carrier such as gpt-oss-20b can
spend that whole budget thinking and return content=None, which crashes the
driver and reads as "did not run". A constant low effort keeps the carrier
identical across rows (REQ-MD-01) while leaving the app's own prompt and
token budget untouched.

3. When a test sets `pin_model` (GUARDRAIL_PIN_MODEL=<id>), overrides the model
   on every chat completion. Lab 05 auto-picks the FIRST id from /models, which
   on OpenRouter is arbitrary; REQ-MD-01 needs the same carrier on every row.

5. EXTRA_HEADERS (extra_headers.py): adds the headers configured for the endpoint
   the lab's client calls (a local gateway's model header, a vendor gateway's
   project id). A malformed table or an unset ${VAR} is reported on stderr and
   no extra header is sent; run_evals.sh (run_meta.py check-headers) and the notebook
   refuse such a table before a run. Sync chat completions only, like the layers below.
"""

import json
import os
import sys
import time

_headers_raw = os.environ.get("EXTRA_HEADERS", "")
if _headers_raw.strip():
    try:
        import extra_headers as _eh

        _header_table = _eh.resolve(_eh.parse(_headers_raw), os.environ)
        from openai.resources.chat.completions import Completions

        _bare_create = Completions.create

        def _headered(self, *args, **kwargs):
            add = _eh.for_url(_header_table, self._client.base_url)
            if add:
                kwargs["extra_headers"] = {**add, **dict(kwargs.pop("extra_headers", None) or {})}
            return _bare_create(self, *args, **kwargs)

        Completions.create = _headered
    except Exception as e:  # noqa: BLE001 - never break the lab over the shim
        print(f"[shim] EXTRA_HEADERS not applied: {type(e).__name__}: {e}", file=sys.stderr)

_route = [p.strip() for p in os.environ.get("OPENROUTER_PROVIDER_ONLY", "").split(",") if p.strip()]
if _route:
    # 4. Pin the OpenRouter PROVIDER too (REQ-MD-01): the same model id is served by
    #    several providers at different quantisations, and tool support is only a
    #    best-effort routing preference. require_parameters makes tools mandatory.
    try:
        from openai.resources.chat.completions import Completions

        _unrouted_create = Completions.create

        def _routed(self, *args, **kwargs):
            extra = dict(kwargs.pop("extra_body", None) or {})
            extra.setdefault("provider", {"only": _route, "allow_fallbacks": False,
                                          "require_parameters": True})
            return _unrouted_create(self, *args, extra_body=extra, **kwargs)

        Completions.create = _routed
    except Exception:  # noqa: BLE001 - never break the lab over the shim
        pass

_pin = os.environ.get("GUARDRAIL_PIN_MODEL")
if _pin:
    try:
        from openai.resources.chat.completions import Completions

        _unpinned_create = Completions.create

        def _pinned(self, *args, **kwargs):
            kwargs["model"] = _pin
            return _unpinned_create(self, *args, **kwargs)

        Completions.create = _pinned
    except Exception:  # noqa: BLE001 - never break the lab over the shim
        pass

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

        _resample = int(os.environ.get("GUARDRAIL_RESAMPLE", "0") or 0)
        _trial = os.environ.get("GUARDRAIL_TRIAL_ID")

        def _record(kwargs, response, n, started_at):
            try:
                choices = getattr(response, "choices", None) or []
                entry = {
                    "trial_id": _trial,
                    "started_at": started_at,
                    "finished_at": time.time(),
                    "resample": n,
                    "messages": kwargs.get("messages"),
                    "tools": kwargs.get("tools"),
                    "reply": choices[0].message if choices else None,
                    "finish_reason": getattr(choices[0], "finish_reason", None) if choices else None,
                    "usage": getattr(response, "usage", None),
                }
                with open(_transcript, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, default=_plain) + "\n")
            except Exception:  # noqa: BLE001 - recording must never break the lab
                pass

        def _empty(response):
            choices = getattr(response, "choices", None) or []
            msg = choices[0].message if choices else None
            return not ((getattr(msg, "content", None) or "").strip()
                        or getattr(msg, "tool_calls", None))

        def _recorded(self, *args, **kwargs):
            if _trial:
                headers = dict(kwargs.pop("extra_headers", None) or {})
                headers.setdefault("X-Trial-Id", _trial)
                kwargs["extra_headers"] = headers
            n = 0
            while True:
                started_at = time.time()
                response = _inner_create(self, *args, **kwargs)
                _record(kwargs, response, n, started_at)
                if kwargs.get("stream") or n >= _resample or not _empty(response):
                    return response
                choices = getattr(response, "choices", None) or []
                # No choices = an endpoint error, not an empty turn: hand it to the lab
                # (its crash reads NOT-RUN and the provider retries the attempt).
                if not choices or choices[0].finish_reason in ("content_filter", "length"):
                    return response
                n += 1

        Completions.create = _recorded
    except Exception:  # noqa: BLE001
        pass
