#!/usr/bin/env python3
"""Build the coverage dashboard's data files from COVERAGE.md + eval results.

Writes dashboard/data/{testcases,rows,history}.json:
  testcases.json  one row per catalog TC-ID (status, priority, latest result)
  rows.json       one row per eval row per run (attempts landed/blocked/not run)
  history.json    dated coverage counts; --snapshot upserts today's counts

Usage: scripts/coverage_data.py [--results DIR] [--snapshot [YYYY-MM-DD]]
Re-run after editing COVERAGE.md or landing a new run under eval-results/,
then upload the three files to the dashboard.
"""
import argparse, datetime, glob, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dashboard", "data")
STATUS = {"🟢": "Covered", "🟡": "Partial", "🔴": "Not covered"}
CATEGORY = {
    "PI": "Prompt injection", "JB": "Jailbreak", "DLP": "Data loss", "TOX": "Toxicity",
    "POL": "Policy", "OUT": "Output handling", "TOOL": "Tool-call mediation",
    "EXF": "Exfiltration", "MEM": "Memory poisoning", "SUP": "Supply chain",
    "NF": "Non-functional", "MM": "Multimodal", "GSI": "Guardrail self-integrity",
}
USE_CASE = {"RAG": "RAG", "MCP": "MCP", "TOOLS": "Tools"}
ROW_ID = re.compile(r"`((?:RAG|MCP|TOOLS)-[A-Za-z0-9]+)`")


def clean(s):
    return re.sub(r"[*`]", "", s).strip()


def parse_coverage(path):
    """TC-ID rows from both COVERAGE.md tables; a TC-ID split into bands merges."""
    tcs, scope = {}, None
    for line in open(path, encoding="utf-8"):
        if line.startswith("### "):
            scope = "In scope" if "In the three" in line else "Out of scope"
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.startswith("|") or not re.fullmatch(r"[A-Z]+-\d+", cells[0]):
            continue
        tc, scenario, prio, status_cell, target = cells[:5]
        notes = cells[5] if len(cells) > 5 else ""
        status = next((v for k, v in STATUS.items() if k in status_cell), "Not covered")
        rows = ROW_ID.findall(line)
        if tc in tcs:  # second band of the same TC-ID
            t = tcs[tc]
            t["bands"] += 1
            t["scenario"] = re.sub(r" — Band.*", "", t["scenario"])
            if status != t["status"]:
                t["status"] = "Partial"
            t["eval_rows"] = sorted(set(t["eval_rows"].split(", ") + rows) - {""})
            t["eval_rows"] = ", ".join(t["eval_rows"])
            continue
        tcs[tc] = {
            "tc_id": tc, "category": CATEGORY.get(tc.split("-")[0], tc.split("-")[0]),
            "scenario": clean(scenario), "priority": prio, "scope": scope,
            "status": status, "target": clean(target), "notes": clean(notes),
            "eval_rows": ", ".join(rows), "bands": 1,
        }
    return tcs


def classify(reason, passed):
    if "did not run" in reason:
        return "not_run"
    return "blocked" if passed else "landed"


def parse_runs(results_dir):
    """Per eval row per run directory: how many attempts landed / blocked / didn't run."""
    rows = []
    for run in sorted(glob.glob(os.path.join(results_dir, "*/"))):
        name = os.path.basename(run.rstrip("/"))
        date = name[:10]
        agg = {}

        def add(desc, outcome):
            rid = desc.split()[0]
            r = agg.setdefault(desc, {
                "id": f"{name}/{rid}" + ("-defended" if " defended" in desc else ""),
                "run": name, "date": date, "row_id": rid,
                "use_case": USE_CASE.get(rid.split("-")[0], ""),
                "label": desc.split(" — ", 1)[-1] if " — " in desc else desc,
                "defended": " defended" in desc,
                "landed": 0, "blocked": 0, "not_run": 0,
            })
            r[outcome] += 1

        jsons = [f for f in glob.glob(run + "*.json") if not os.path.basename(f).startswith("proof-")]
        for f in jsons:
            data = json.load(open(f))
            for res in data.get("results", {}).get("results", []):
                desc = (res.get("testCase") or {}).get("description") or res.get("description", "")
                g = res.get("gradingResult") or {}
                add(desc, classify(g.get("reason", res.get("error", "")), g.get("pass")))
        summary = run + "summary.txt"
        if not jsons and os.path.exists(summary):  # single-row runs log only a summary
            lines = open(summary, encoding="utf-8").read().splitlines()
            m = re.search(r"result: (PASS|FAIL|NOT-RUN)", "\n".join(lines))
            if lines and m:
                add(lines[0], {"PASS": "blocked", "FAIL": "landed"}.get(m.group(1), "not_run"))
        rows.extend(agg.values())
    for r in rows:
        r["attempts"] = r["landed"] + r["blocked"] + r["not_run"]
        if r["landed"]:
            r["outcome"] = "Defense bypassed" if r["defended"] else "Attack landed"
        elif r["blocked"]:
            r["outcome"] = "Defense held" if r["defended"] else "Attack blocked"
        else:
            r["outcome"] = "Didn't run"
    return rows


def latest_results(tcs, rows):
    """Each TC-ID's latest result: newest run per eval row, undefended vs defended."""
    latest = {}
    for r in rows:
        k = (r["row_id"], r["defended"])
        if k not in latest or r["run"] > latest[k]["run"]:
            latest[k] = r
    for t in tcs.values():
        ids = t["eval_rows"].split(", ") if t["eval_rows"] else []
        und = [latest[(i, False)] for i in ids if (i, False) in latest]
        dfd = [latest[(i, True)] for i in ids if (i, True) in latest]
        t["last_run"] = max((r["date"] for r in und + dfd), default="")
        if und:
            n = sum(r["landed"] for r in und)
            a = sum(r["attempts"] for r in und)
            t["attack"] = f"Landed {n}/{a}" if n else f"Blocked {a}/{a}" if a else "Didn't run"
        else:
            t["attack"] = ""
        if dfd:
            t["defense"] = "Bypassed" if any(r["landed"] for r in dfd) else "Holds"
        else:
            t["defense"] = "None in lab" if und else ""


def counts(tcs):
    c = {"covered": 0, "partial": 0, "not_covered": 0}
    for t in tcs.values():
        c[t["status"].lower().replace(" ", "_")] += 1
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", default=os.path.join(ROOT, "COVERAGE.md"))
    ap.add_argument("--results", default="/mnt/project-files/eval-results")
    ap.add_argument("--snapshot", nargs="?", const=datetime.date.today().isoformat())
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    tcs = parse_coverage(a.coverage)
    rows = parse_runs(a.results)
    latest_results(tcs, rows)

    hist_path = os.path.join(OUT, "history.json")
    history = json.load(open(hist_path)) if os.path.exists(hist_path) else []
    if a.snapshot:
        history = [h for h in history if h["date"] != a.snapshot]
        history.append({"date": a.snapshot, **counts(tcs), "total": len(tcs),
                        "note": "Snapshot of COVERAGE.md"})
        history.sort(key=lambda h: h["date"])

    order = {"Covered": 0, "Partial": 1, "Not covered": 2}
    out = sorted(tcs.values(), key=lambda t: (order[t["status"]], t["priority"], t["tc_id"]))
    for name, data in (("testcases", out), ("rows", rows), ("history", history)):
        with open(os.path.join(OUT, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
            f.write("\n")
    c = counts(tcs)
    print(f"{len(tcs)} TC-IDs: {c['covered']} covered, {c['partial']} partial, "
          f"{c['not_covered']} not covered; {len(rows)} eval rows across runs")


if __name__ == "__main__":
    main()
