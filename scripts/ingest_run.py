#!/usr/bin/env python3
"""Load one eval run into the results store (design: docs/RESULTS_DB.md).

    python3 scripts/ingest_run.py results/runs/<run_id> [--db results/guardrail_eval.db]

The run dir is written by scripts/run_evals.sh:
    run.json            run context (config, git, carrier, guardrail, repeat, filter, ...)
    <config>.json       promptfoo output (immutable)
    slot_log.jsonl      guardrail-slot wire log (model_channel only)

Creates the DB from db/schema.sql if it doesn't exist. Append-only: a run_id that is
already in the DB is skipped. Each ingest also records the workbook catalog (once per
version) and a COVERAGE.md snapshot (only when it changed since the last one).

Verdicts come from the provider's `outcome` (promptfoo/providers/outcome.py, the same
rule summarize.py and assert_canary.py use); ERROR if promptfoo errored with no output.
The outcome, delivery, detection and intercept columns are stored with each trial, and
each enforcement event becomes a `block_event` evidence item. Transcripts are stored in
full. A store at an older schema version is migrated with db/NNN_*.sql first.

Env NAME_FLAG_REGEX (optional, set it in .env): a regex for names that must not appear
in reports. Matching transcripts get flags='name-match'. It is read from the
environment so the names themselves are never written into the repo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "promptfoo" / "providers"))
import outcome  # noqa: E402  (one grading rule)

SCHEMA = ROOT / "db" / "schema.sql"
DEFAULT_DB = ROOT / "results" / "guardrail_eval.db"
WORKBOOK = ROOT / "AI_Guardrails_POC_4_2.xlsx"
CATALOG_VERSION = "4.2"

USE_CASE = {"mcp": "MCP", "rag": "RAG", "tools": "Tools", "web": "Web",
            "web_server": "Web", "model_channel": "Model channel", "redteam": "Redteam"}
# Oracle ladder (Success Oracles tab): 1 = OOB/side channel ... 5 = judge.
# Oracle-ladder tier (CLAUDE.md): 1 = OOB callback, 2 = canary, 3 = state/tool
# log, 4 = answer/regex, 5 = LLM judge. Used as reporting metadata (trial.oracle_rank).
ORACLE_RANK = {"http_callback": 1, "exfil_log": 1, "canary": 2, "pii_forwarded": 2,
               "deleted": 3, "retrieval": 3, "file_contains": 3, "answer": 4,
               "canary_in_reply": 4, "overblocked": 2, "llm-rubric": 5}
SINK_KIND = {"exfil": "oob_exfil", "email_log": "email_log", "retrievals": "retrieval_log"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


# One canonical .env parser (proxy.load_dotenv), shared with the slot and the providers.
sys.path.insert(0, str(ROOT / "promptfoo" / "guardrail_slot"))
from proxy import load_dotenv  # noqa: E402

_DOTENV: dict[str, str] = {}
load_dotenv(_DOTENV)


def dotenv(key: str) -> str | None:
    """A value from the environment, else from .env (via the shared parser)."""
    return os.environ.get(key) or _DOTENV.get(key) or None


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()


def connect(db: Path) -> sqlite3.Connection:
    new = not db.exists()
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    schema_sql = SCHEMA.read_text(encoding="utf-8")
    if new:
        con.executescript(schema_sql)
    want = int(re.search(r"user_version\s*=\s*(\d+)", schema_sql).group(1))
    have = con.execute("PRAGMA user_version").fetchone()[0]
    for mig in sorted(SCHEMA.parent.glob("[0-9][0-9][0-9]_*.sql")):
        if have < int(mig.name[:3]) <= want:
            con.executescript(mig.read_text(encoding="utf-8"))  # sets user_version itself
            print(f"{db}: migrated to schema {mig.name[:3]} ({mig.name})")
    have = con.execute("PRAGMA user_version").fetchone()[0]
    if have != want:
        sys.exit(f"{db}: schema version {have}, db/schema.sql is {want}; no migration path")
    return con


# ── catalog + coverage ────────────────────────────────────────────────────────
def _col(ref: str) -> str:
    return re.match(r"[A-Z]+", ref).group(0)


def load_catalog(con: sqlite3.Connection) -> None:
    if con.execute("SELECT 1 FROM catalog WHERE version = ?", (CATALOG_VERSION,)).fetchone():
        return
    if not WORKBOOK.exists():
        print(f"note: {WORKBOOK.name} not found; catalog not loaded", file=sys.stderr)
        return
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    z = zipfile.ZipFile(WORKBOOK)
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    sheet = next(s for s in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns)
                 if s.get("name") == "Use Cases")
    root = ET.fromstring(z.read("xl/" + rels[sheet.get(rid)].lstrip("/").replace("xl/", "")))
    header, cases = None, []
    for row in root.iter(f"{{{ns['m']}}}row"):
        # Cells are sparse: key them by column letter, not position.
        cells = {_col(c.get("r")): "".join(x.text or "" for x in c.iter() if x.tag.endswith("}t"))
                 for c in row.findall("m:c", ns)}
        if header is None:
            if cells.get("A") == "TC-ID":
                header = {v.strip(): k for k, v in cells.items()}
            continue
        if re.match(r"^[A-Z]+-\d", cells.get("A", "")):
            get = lambda name: cells.get(header.get(name, ""), "").strip()
            cases.append((get("TC-ID"), CATALOG_VERSION, get("Category"), get("Scenario (Use Case)"),
                          get("Priority"), get("Guardrail Scope"), get("Attack Target"),
                          get("Measurable Success Criteria") or None))
    con.execute("INSERT INTO catalog VALUES (?, ?, ?)", (CATALOG_VERSION, WORKBOOK.name, now()))
    con.executemany("INSERT INTO test_case VALUES (?, ?, ?, ?, ?, ?, ?, ?)", cases)


def coverage_rows() -> list[tuple[str, str, str, str, str, list[str]]]:
    """(tc_id, status, section, target, notes, row-id tokens) per TC-ID in COVERAGE.md.

    Reads only the tables under "## Coverage by TC-ID"; `section` is the `### <family>`
    heading above each table. Columns: TC-ID | Scenario | P | Status | Harness | Rows.
    Backticked row-ID tokens (`MC-JB03`, `RAG-PI02`) link eval rows to the TC-ID by
    prefix, so every covered TC-ID must name its rows that way."""
    status_of = {"🟢": "Covered", "🟡": "Partial", "🔴": "Not covered"}
    out, in_cov, section = {}, False, None
    for line in (ROOT / "COVERAGE.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_cov, section = line.startswith("## Coverage by TC-ID"), None
            continue
        if in_cov and line.startswith("### "):
            section = line[4:].strip()
            continue
        if not section or not re.match(r"^\| [A-Z]+-\d", line):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        tc = cols[0]
        status = next((v for k, v in status_of.items() if k in cols[3]), "Not covered")
        target, notes = (cols[4], cols[5]) if len(cols) > 5 else ("", cols[4] if len(cols) > 4 else "")
        tokens = re.findall(r"`([A-Z][A-Z0-9]+(?:-[A-Za-z0-9]+)+)`", " ".join(cols[4:]))
        if tc in out:  # a TC-ID split over bands: keep the weaker status, merge the rest
            prev = out[tc]
            rank = ["Not covered", "Partial", "Covered"]
            status = min(status, prev[1], key=rank.index)
            notes = prev[4] + " | " + notes
            tokens = prev[5] + tokens
        out[tc] = (tc, status, section, target, notes, tokens)
    return list(out.values())


def snapshot_coverage(con: sqlite3.Connection, cov: list) -> None:
    current = sorted((tc, st) for tc, st, *_ in cov)
    last = con.execute("SELECT MAX(snapshot_id) FROM coverage_snapshot").fetchone()[0]
    if last is not None:
        prev = sorted(con.execute("SELECT tc_id, status FROM coverage_status WHERE snapshot_id = ?", (last,)))
        if prev == current:
            return
    cur = con.execute("INSERT INTO coverage_snapshot (taken_at, git_commit, git_dirty) VALUES (?, ?, ?)",
                      (now(), git("rev-parse", "HEAD") or None, int(bool(git("status", "--porcelain", "COVERAGE.md")))))
    con.executemany("INSERT INTO coverage_status VALUES (?, ?, ?, ?, ?, ?)",
                    [(cur.lastrowid, tc, st, sec, tgt, notes) for tc, st, sec, tgt, notes, _ in cov])


# ── rows, trials, transcripts, evidence ───────────────────────────────────────
def verdict(r: dict, out: dict) -> str:
    if out.get("outcome"):
        return outcome.verdict_of(out["outcome"])
    if not r.get("gradingResult"):
        return "ERROR"
    # Runs from before outcome.py (schema v1) carry only the old reason string.
    g = r["gradingResult"]
    return "NOT-RUN" if "did not run" in (g.get("reason") or "") else ("PASS" if g.get("pass") else "FAIL")


def row_definition(config: str, desc: str, v: dict) -> tuple:
    row_id = desc.split()[0] if desc else "UNNAMED"
    is_cal = row_id.startswith("CAL-")
    oracle = v.get("oracle") or "canary"
    # The providers' own markers, never the description: attack rows say "benign" too.
    is_control = str(v.get("benign", "")).lower() in ("1", "true", "yes") \
        or v.get("family") == "control" or oracle == "overblocked"
    defense = v.get("app_control") or v.get("defense")  # `defense` = rows from before app_control
    defended = bool(defense)
    if defended:  # twins share their first word with the baseline: suffix the control
        row_id += "-" + re.sub(r"[^A-Za-z0-9]+", "-", str(defense)).strip("-")
    m = re.search(r"\(expect (PASS|FAIL)", desc)
    expected = m.group(1) if m else ("PASS" if is_control else "FAIL")
    family = re.sub(r"-?\d+$", "", row_id) if config == "model_channel" else None
    return (row_id, config, USE_CASE.get(config, config), family, desc, v.get("lab"), oracle,
            ORACLE_RANK.get(oracle, 3), int(defended), defense, int(is_control), int(is_cal), expected)


def flag01(value) -> int | None:
    return None if value is None else int(bool(value))


def name_flag(text: str) -> str | None:
    rx = dotenv("NAME_FLAG_REGEX")
    return "name-match" if rx and re.search(rx, text, re.I) else None


def evidence_items(out: dict, reason: str) -> list[tuple[str, str, str | None, str | None]]:
    items = [("block_event", f"{e.get('held_by')} {e.get('action')} at {e.get('hop')}: {e.get('subject')}",
              json.dumps(e, ensure_ascii=False), "stdout" if str(e.get("held_by", "")).startswith("app:")
              else "slot_log.jsonl") for e in out.get("events") or []]
    for path, content in (out.get("sink") or {}).items():
        kind = next((k for key, k in SINK_KIND.items() if key in path), "state_file")
        text = content if isinstance(content, str) else json.dumps(content)
        items.append((kind, f"{path}: {len(text)} bytes captured", text, path))
    for attempt in out.get("evidence_attempts") or []:
        items.append(("delivery_trace",
                      f"attempt {attempt['attempt']} ({attempt['trial_id']}): full model/OOB evidence",
                      json.dumps(attempt, ensure_ascii=False), "response.output.evidence_attempts"))
    m = re.search(r"server logged callback: (\{.*\})", reason or "")
    if m:
        items.append(("http_callback", "server logged a /collect callback", m.group(1), "results/web_server/hits.jsonl"))
    return items


def ingest(run_dir: Path, db: Path) -> None:
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    config = meta["config"]
    con = connect(db)
    with con:
        if con.execute("SELECT 1 FROM run WHERE run_id = ?", (meta["run_id"],)).fetchone():
            print(f"{meta['run_id']}: already ingested, skipped")
            return
        load_catalog(con)
        cov = coverage_rows()
        snapshot_coverage(con, cov)

        gid = meta.get("guardrail_id") or "none"
        con.execute("INSERT OR IGNORE INTO guardrail (guardrail_id, vendor, placement) VALUES (?, ?, ?)",
                    (gid, gid if gid in ("none", "demo") else gid.split("-")[0],
                     "none" if gid == "none" else meta.get("placement", "none")))

        pf_path = run_dir / f"{config}.json"
        pf = json.loads(pf_path.read_text(encoding="utf-8")) if pf_path.exists() else None
        status = meta.get("status") or ("complete" if pf else "aborted")
        con.execute("INSERT INTO run (run_id, config, started_at, finished_at, status, git_commit, git_dirty,"
                    " promptfoo_version, promptfoo_eval_id, carrier_model, provider_pin, reasoning_effort,"
                    " guardrail_id, repeat, filter, concurrency, raw_dir, stats_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (meta["run_id"], config, meta["started_at"], meta.get("finished_at"), status,
                     meta["git_commit"], int(meta["git_dirty"]), meta["promptfoo_version"],
                     (pf or {}).get("evalId"), meta["carrier_model"], meta.get("provider_pin") or None,
                     meta.get("reasoning_effort"), gid, int(meta["repeat"]), meta.get("filter") or None,
                     int(meta["concurrency"]), rel(run_dir),
                     json.dumps((pf or {}).get("results", {}).get("stats")) if pf else None))
        if not pf:
            print(f"{meta['run_id']}: no promptfoo output; recorded as {status}")
            return

        slot = {}
        if (run_dir / "slot_log.jsonl").exists():
            for line in (run_dir / "slot_log.jsonl").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    slot[e.get("trial_id")] = e  # last one wins on retries

        attempts: dict[str, int] = {}
        counts = {"trials": 0, "flagged": 0}
        for r in pf["results"]["results"]:
            tc = r.get("testCase") or {}
            desc, v = tc.get("description", ""), (tc.get("vars") or r.get("vars") or {})
            rowdef = row_definition(config, desc, v)
            row_id = rowdef[0]
            con.execute("INSERT OR IGNORE INTO eval_row VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rowdef)
            if not rowdef[11]:  # calibration rows never map to a TC-ID
                for tc_id, *_, tokens in cov:
                    if any(row_id == t or row_id.startswith(t) for t in tokens):
                        con.execute("INSERT OR IGNORE INTO row_tc VALUES (?, ?)", (row_id, tc_id))
            attempts[row_id] = attempts.get(row_id, 0) + 1

            out = (r.get("response") or {}).get("output")
            out = out if isinstance(out, dict) else {}
            g = r.get("gradingResult") or {}
            reason = g.get("reason") or r.get("error") or ""
            tok = r.get("tokenUsage") or {}
            cur = con.execute(
                "INSERT INTO trial (run_id, row_id, attempt, row_vars_sha256, verdict, canary_reached, ran,"
                " reason, guardrail_action, latency_ms, cost_usd, tokens_prompt, tokens_completion,"
                " outcome, delivered, detected, intercept_action, held_by, hop, resamples)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (meta["run_id"], row_id, attempts[row_id], sha(json.dumps(v, sort_keys=True, default=str)),
                 verdict(r, out), None if "canary_reached" not in out else int(bool(out["canary_reached"])),
                 None if "ran" not in out else int(bool(out["ran"])), reason,
                 out.get("slot_action"), r.get("latencyMs"), r.get("cost"),
                 tok.get("prompt") or None, tok.get("completion") or None,
                 out.get("outcome"), flag01(out.get("delivered")), flag01(out.get("detected")),
                 out.get("intercept_action"), out.get("held_by"), out.get("hop"), out.get("resamples")))
            trial_id = cur.lastrowid
            counts["trials"] += 1

            wire = slot.get(out.get("trial_id")) if out.get("trial_id") else None
            if out.get("transcript"):
                fmt, conversation = "lab-compact-v1", json.dumps(out["transcript"], ensure_ascii=False)
            else:
                fmt = "model-channel-v1"
                conversation = json.dumps({"prompt": (r.get("prompt") or {}).get("raw"),
                                           "request": (wire or {}).get("received"),
                                           "reply": (wire or {}).get("reply") or out.get("answer")},
                                          ensure_ascii=False)
            answer = out.get("answer") or ""
            forwarded = json.dumps(wire.get("forwarded"), ensure_ascii=False) if wire else None
            whole = conversation + answer + (forwarded or "")
            flag = name_flag(whole)
            counts["flagged"] += bool(flag)
            con.execute("INSERT INTO transcript VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (trial_id, fmt, conversation, forwarded,
                         answer if fmt == "model-channel-v1" else None,
                         answer[-20000:] if fmt == "lab-compact-v1" else None,
                         sha(whole), len(whole.encode("utf-8")), flag))

            items = evidence_items(out, reason)
            if wire:
                items.append(("slot_wire", f"slot {wire.get('mode')}: {wire.get('action')}"
                              + (f" ({wire.get('reason')})" if wire.get("reason") else ""),
                              forwarded, "slot_log.jsonl"))
            con.executemany("INSERT INTO evidence (trial_id, kind, summary, payload, source_path) VALUES (?, ?, ?, ?, ?)",
                            [(trial_id, *it) for it in items])

    print(f"{meta['run_id']}: {counts['trials']} trials ingested into {db}"
          + (f"; {counts['flagged']} transcripts flagged name-match" if counts["flagged"] else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    ingest(a.run_dir.resolve(), a.db)


if __name__ == "__main__":
    main()
