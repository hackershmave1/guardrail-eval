"""
Per-endpoint extra headers (EXTRA_HEADERS): one parser for the shim, the guardrail
slot, run_meta.py and the operator notebook.

EXTRA_HEADERS is one-line JSON mapping an endpoint base URL to the headers it needs:

    EXTRA_HEADERS={"http://localhost:4000/v1": {"x-model": "gpt-oss-20b"},
                   "https://gw.vendor.example/v1": {"x-project-id": "${VENDOR_PROJECT_ID}"}}

A request gets the headers of every base URL that is a prefix of its target (on a
path boundary); a longer base URL wins a clash. `${VAR}` is filled from the
environment / .env, so a secret can live in its own variable. An unset VAR is an
error, never an empty header. Error messages name keys and variables, never values.

Stdlib only: the shim imports this inside every lab subprocess.
"""

from __future__ import annotations

import json
import re

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def parse(raw: str | None) -> dict[str, dict[str, str]]:
    """{base URL: {header: raw value}} from the EXTRA_HEADERS string ({} when unset)."""
    if not raw or not raw.strip():
        return {}
    table = json.loads(raw)
    if not isinstance(table, dict) or not all(isinstance(h, dict) for h in table.values()):
        raise ValueError("EXTRA_HEADERS must be a JSON object of {base URL: {header: value}}")
    return {str(url).rstrip("/"): {str(k): str(v) for k, v in h.items()} for url, h in table.items()}


def missing_vars(table: dict[str, dict[str, str]], env: dict) -> list[str]:
    """The ${VAR} names the table references that env does not set."""
    refs = {m for h in table.values() for v in h.values() for m in _VAR.findall(v)}
    return sorted(r for r in refs if not env.get(r))


def resolve(table: dict[str, dict[str, str]], env: dict) -> dict[str, dict[str, str]]:
    """The table with every ${VAR} filled from env. Raises KeyError naming unset vars."""
    missing = missing_vars(table, env)
    if missing:
        raise KeyError(f"EXTRA_HEADERS references unset variable(s): {', '.join(missing)}")
    return {url: {k: _VAR.sub(lambda m: env[m.group(1)], v) for k, v in h.items()}
            for url, h in table.items()}


def for_url(table: dict[str, dict[str, str]], url) -> dict[str, str]:
    """Headers for a request to `url` (a resolved table; shortest base URL first)."""
    target = str(url).rstrip("/")
    out: dict[str, str] = {}
    for base in sorted(table, key=len):
        if target == base or target.startswith(base + "/"):
            out.update(table[base])
    return out


def names(table: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    """{base URL: [header names]}: what may be shown or recorded (never the values)."""
    return {url: sorted(h) for url, h in table.items()}
