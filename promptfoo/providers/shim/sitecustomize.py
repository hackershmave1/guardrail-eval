"""
Auto-imported (via PYTHONPATH) into every lab subprocess the provider runs.

1. Transcript recorder (GUARDRAIL_TRANSCRIPT=<path>): appends each chat
   completion's request (messages, tool definitions) and the model's reply as a
   JSON line, so every row carries proof of what the model was shown (the
   poisoned tool description, the injected tool result, the retrieved doc) and
   what it did with it. The request is recorded as the lab sent it: the pre-image
   the delivery check reads (outcome.py). Each line also carries finish_reason,
   usage, the trial id, the resample index and the upstream that served the call
   (OpenRouter's `provider` field; None from an endpoint that doesn't name one).

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

4. OPENROUTER_PROVIDER_ONLY pins the OpenRouter PROVIDER too (REQ-MD-01): the same
   model id is served by several providers at different quantisations, and tool
   support is only a best-effort routing preference. require_parameters makes
   tools mandatory.

5. EXTRA_HEADERS (extra_headers.py): adds the headers configured for the endpoint
   the lab's client calls (a local gateway's model header, a vendor gateway's
   project id). A malformed table or an unset ${VAR} is reported on stderr and
   no extra header is sent; run_evals.sh (run_meta.py check-headers) and the notebook
   refuse such a table before a run. Sync chat completions only, like the layers below.

Each layer wraps the `create` inside it; install() stacks them in the order above.
"""

import json
import os
import sys
import time


def _headered(create, table):
    import extra_headers as _eh

    def call(self, *args, **kwargs):
        add = _eh.for_url(table, self._client.base_url)
        if add:
            kwargs["extra_headers"] = {**add, **dict(kwargs.pop("extra_headers", None) or {})}
        return create(self, *args, **kwargs)
    return call


def _with_body(create, key, value):
    """Send extra_body[key] = value unless the lab set that key itself."""
    def call(self, *args, **kwargs):
        extra = dict(kwargs.pop("extra_body", None) or {})
        extra.setdefault(key, value)
        return create(self, *args, extra_body=extra, **kwargs)
    return call


def _pinned(create, model):
    def call(self, *args, **kwargs):
        kwargs["model"] = model
        return create(self, *args, **kwargs)
    return call


def _plain(obj):
    dump = getattr(obj, "model_dump", None)
    return dump(exclude_none=True) if callable(dump) else str(obj)


def _record(path, trial, kwargs, response, n, started_at):
    try:
        choices = getattr(response, "choices", None) or []
        entry = {
            "trial_id": trial,
            "started_at": started_at,
            "finished_at": time.time(),
            "resample": n,
            "messages": kwargs.get("messages"),
            "tools": kwargs.get("tools"),
            "reply": choices[0].message if choices else None,
            "finish_reason": getattr(choices[0], "finish_reason", None) if choices else None,
            "usage": getattr(response, "usage", None),
            "provider": getattr(response, "provider", None),  # the upstream OpenRouter routed to
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=_plain) + "\n")
    except Exception:  # noqa: BLE001 - recording must never break the lab
        pass


def _empty_turn(response) -> bool:
    """No text and no tool call, on a turn that may be resampled.

    No choices = an endpoint error, not an empty turn: the lab gets it (its crash
    reads NOT-RUN and the provider retries the attempt). `content_filter` and
    `length` are endings, not glitches.
    """
    choices = getattr(response, "choices", None) or []
    if not choices or choices[0].finish_reason in ("content_filter", "length"):
        return False
    msg = choices[0].message
    return not ((getattr(msg, "content", None) or "").strip() or getattr(msg, "tool_calls", None))


def _recorded(create, path, trial, resample):
    def call(self, *args, **kwargs):
        if trial:
            headers = dict(kwargs.pop("extra_headers", None) or {})
            headers.setdefault("X-Trial-Id", trial)
            kwargs["extra_headers"] = headers
        for n in range(resample + 1):
            started_at = time.time()
            response = create(self, *args, **kwargs)
            _record(path, trial, kwargs, response, n, started_at)
            if kwargs.get("stream") or not _empty_turn(response):
                break
        return response
    return call


def _header_layer(env):
    raw = env.get("EXTRA_HEADERS", "")
    if not raw.strip():
        return None
    try:
        import extra_headers as _eh

        table = _eh.resolve(_eh.parse(raw), env)
    except Exception as e:  # noqa: BLE001 - never break the lab over the shim
        print(f"[shim] EXTRA_HEADERS not applied: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    return lambda create: _headered(create, table)


def _layers(env) -> list:
    """The wrappers the env asks for, innermost first."""
    layers = [layer for layer in (_header_layer(env),) if layer]
    route = [p.strip() for p in env.get("OPENROUTER_PROVIDER_ONLY", "").split(",") if p.strip()]
    if route:
        provider = {"only": route, "allow_fallbacks": False, "require_parameters": True}
        layers.append(lambda create: _with_body(create, "provider", provider))
    if env.get("GUARDRAIL_PIN_MODEL"):
        layers.append(lambda create: _pinned(create, env["GUARDRAIL_PIN_MODEL"]))
    if env.get("GUARDRAIL_REASONING_EFFORT"):
        effort = {"effort": env["GUARDRAIL_REASONING_EFFORT"]}
        layers.append(lambda create: _with_body(create, "reasoning", effort))
    if env.get("GUARDRAIL_TRANSCRIPT"):
        resample = max(0, int(env.get("GUARDRAIL_RESAMPLE", "0") or 0))
        layers.append(lambda create: _recorded(create, env["GUARDRAIL_TRANSCRIPT"],
                                               env.get("GUARDRAIL_TRIAL_ID"), resample))
    return layers


def install(env) -> None:
    layers = _layers(dict(env))
    if not layers:
        return
    from openai.resources.chat.completions import Completions

    create = Completions.create
    for layer in layers:
        create = layer(create)
    Completions.create = create


try:
    install(os.environ)
except Exception as e:  # noqa: BLE001 - never break the lab over the shim
    print(f"[shim] not installed: {type(e).__name__}: {e}", file=sys.stderr)
