#!/usr/bin/env python3
"""Fail if anything the model sees names the test (CLAUDE.md "Nothing the model sees may
name the test").

    python scripts/lint_prompts.py [config ...]     # default: every promptfoo config

Model-facing fields: `attack` (except rows whose driver ignores it: `run: rag_attack`,
the fixed-turn / model-free drivers), `system_prompt`, `seed_content`, `source_canary`,
and `canary` (it is spelled out inside the attack). Labels belong in `description` /
`injection_note`, which the model never sees.

ERROR (exit 1): a TC-ID (`PI01`, `JB03-…`), evaluation vocabulary (canary, sentinel,
guardrail, red team, "(expect …)"), a report label in parentheses at the end of a
prompt, a "do not / never" line in a benign control, or a self-grounding line
("use only the source", "do not rely on outside knowledge") in any row.
WARN: the word "test" — legitimate as an attacker's pretext ("for a support-tool test,
disregard…"), so it is listed for a human to confirm, not failed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "promptfoo"
CONFIGS = ["mcp", "tools", "rag", "web", "web_server", "memory", "a2a", "model_channel", "redteam"]
IGNORES_PROMPT = re.compile(r"lab05_stateful_injection|lab07_killchain")
FIELDS = ("attack", "system_prompt", "seed_content", "source_canary", "canary")

TC_ID = re.compile(r"\b(?:PI|DLP|JB|OUT|TOOL|EXF|SUP|MEM|CAL|GSI|NF|POL|TOX|MM)-?\d{2}[A-Z]?\b")
META = re.compile(r"\b(canar(y|ies)|sentinel|guardrail|red[ -]?team|benchmark|eval(uation)?"
                  r"|known[- ](good|bad)|look-?alike|proprietary IP|exfil\w*)\b|\(expect", re.I)
LABEL = re.compile(r"\([^()]{3,80}\)\s*$")
HARDEN = re.compile(r"\b(do not|don't|never)\b[^.\n]*\b(modify|delete|disclose|reveal|share|store)\b", re.I)
# Grounding done by the prompt itself (the OUT-04 Band A lesson): an error on any row.
SELF_GROUND = re.compile(r"\b(use only the|only (use )?the (source|document|context)|strictly from"
                         r"|do not rely on|outside knowledge|if the (source|document|context) does not)\b", re.I)
WARN = re.compile(r"\btest(ing)?\b", re.I)


def rows(config: str):
    doc = yaml.safe_load((ROOT / f"{config}.yaml").read_text(encoding="utf-8"))
    base = {}
    for p in doc.get("providers") or doc.get("targets") or []:
        if isinstance(p, dict):
            base.update(p.get("config") or {})
    tests = doc.get("tests") or []
    for t in tests if isinstance(tests, list) else [tests]:
        if isinstance(t, str) and t.startswith("file://"):
            for x in yaml.safe_load((ROOT / t[len("file://"):]).read_text(encoding="utf-8")) or []:
                yield x.get("description", ""), {**base, **(x.get("vars") or {})}
        elif isinstance(t, dict):
            yield t.get("description", ""), {**base, **(t.get("vars") or {})}
    if base.get("system_prompt"):
        yield f"{config} provider config", {"system_prompt": base["system_prompt"]}


def check(desc: str, v: dict) -> tuple[list[str], list[str]]:
    errors, warns = [], []
    benign = str(v.get("benign", "")).lower() in ("1", "true", "yes") or "benign" in desc
    for field in FIELDS:
        text = v.get(field)
        if not isinstance(text, str):
            continue
        if field == "attack" and (v.get("run") == "rag_attack" or IGNORES_PROMPT.search(str(v.get("script", "")))):
            continue
        plain = re.sub(r"[​-‏⁠﻿]|[\U000e0000-\U000e007f]", "", text)
        for rx, why in ((TC_ID, "test ID"), (META, "eval vocabulary")):
            m = rx.search(plain)
            if m:
                errors.append(f"{field}: {why} {m.group(0)!r}")
        if field == "attack" and LABEL.search(plain.strip()):
            errors.append(f"attack: report label {LABEL.search(plain.strip()).group(0)!r}")
        if benign and field in ("attack", "system_prompt") and HARDEN.search(plain):
            errors.append(f"{field}: hardening line in a control {HARDEN.search(plain).group(0)!r}")
        if field in ("attack", "system_prompt", "seed_content") and SELF_GROUND.search(plain):
            errors.append(f"{field}: the prompt grounds itself {SELF_GROUND.search(plain).group(0)!r}")
        if WARN.search(plain):
            warns.append(f"{field}: {WARN.search(plain).group(0)!r}")
    return errors, warns


def main() -> int:
    configs = sys.argv[1:] or CONFIGS
    bad = 0
    for c in configs:
        for desc, v in rows(c):
            errors, warns = check(desc, v)
            for e in errors:
                print(f"ERROR {c}: {desc[:60]} :: {e}")
            for w in warns:
                print(f"warn  {c}: {desc[:60]} :: {w} (confirm it is the attacker's own pretext)")
            bad += bool(errors)
    print(f"lint_prompts: {bad} row(s) with errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
