#!/usr/bin/env python3
"""Widen a FILTER regex to every condition of the rows it matches.

    python scripts/pair_filter.py <config> '<regex>'   -> prints a regex

A vendor or app-control twin is credited only against the undefended baseline in
the same run_id (docs/OPEN_QUESTIONS.md A9, rule 7). Twins share the first word
of their description (`MCP-SUP01 undefended` / `MCP-SUP01 defended`), so the
widened filter is `^(<first words>) ` over every description the input matched.
Prints the input unchanged if it matches nothing.
"""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "promptfoo"


def descriptions(config: str) -> list[str]:
    doc = yaml.safe_load((ROOT / f"{config}.yaml").read_text(encoding="utf-8"))
    tests = doc.get("tests") or []
    out = []
    for t in tests if isinstance(tests, list) else [tests]:
        if isinstance(t, str) and t.startswith("file://") and t.endswith((".yaml", ".yml")):
            out += [x.get("description", "") for x in
                    yaml.safe_load((ROOT / t[len("file://"):]).read_text(encoding="utf-8")) or []]
        elif isinstance(t, dict):
            out.append(t.get("description", ""))
    return out


def main() -> None:
    config, pattern = sys.argv[1], sys.argv[2]
    try:
        words = sorted({d.split()[0] for d in descriptions(config) if d and re.search(pattern, d)})
    except (OSError, yaml.YAMLError):
        words = []  # datasets loaded some other way (model_channel): leave the filter alone
    print(f"^({'|'.join(re.escape(w) for w in words)}) " if words else pattern)


if __name__ == "__main__":
    main()
