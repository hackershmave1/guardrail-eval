"""
Driver for the MCP->A2A kill chain (AgenticGoat Lab 07) — the PI-04 A2A variant.

Run by agentic_goat_provider.py with cwd = the Lab 07 dir:
    python <this file> [--control card|authz] "<ignored prompt>"

Lab 07 is model-independent, deterministic control-plane logic: a poisoned MCP
tool description (the Lab 01/06 primitive) bootstraps a five-stage agent-to-agent
kill chain that registers a rogue peer, hijacks routing to it, and uses its
trusted position to invoke a sensitive skill and exfiltrate HR data to
state/exfil.json (carrying the lab flag). `killchain.run_chain` resets its own
state each invocation, so every trial starts clean.

Why a driver (not the lab's run_chain.py directly): the provider appends the
row's prompt as a trailing arg, which the lab's argparse would reject. This
wrapper accepts and ignores it, selects the Controls for the variant, runs the
chain, and prints the stage transcript so `require_trace` can confirm the chain
executed. The pinned submodule is never edited.

Oracle (provider `canary`): the lab's canary.py checks state/exfil.json for the
flag — set only when the undefended chain completes Stage 4 (lateral movement).
Trace: `Stage 1 - Tool description poisoning` prints in every variant (no control
governs Stage 1), so it is True in the undefended run and both defended twins —
upstream of where `card` (Stage 2) or `authz` (Stage 4) breaks the chain.

Controls (defenses.Controls):
  none  — undefended: all five stages succeed, flag exfiltrated (expect FAIL).
  card  — require signed Agent Cards: the rogue can't sign, registration refused
          at Stage 2; the chain collapses (expect PASS).
  authz — authorize the action not just the caller: registration/routing stay
          open but the sensitive skill is denied at Stage 4 (expect PASS).
"""

from __future__ import annotations

import argparse
import os
import sys

LAB_DIR = os.getcwd()
sys.path.insert(0, LAB_DIR)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", choices=["none", "card", "authz"], default="none")
    ap.add_argument("query", nargs="?", default=None)  # provider appends the prompt; ignored
    args = ap.parse_args()

    from defenses import Controls
    from killchain import run_chain

    controls = {
        "none": Controls.none(),
        "card": Controls(require_signed_cards=True),
        "authz": Controls(enforce_authorization=True),
    }[args.control]

    print(f"[driver] control={args.control}", flush=True)
    run = run_chain(controls)  # resets state, runs all five stages
    for r in run.results:
        mark = "attacker-succeeded" if r.attacker_succeeded else "blocked"
        print(f"  Stage {r.number} - {r.name}: {mark} :: {r.detail}", flush=True)
    print(f"[result] compromised={run.compromised}", flush=True)
    return 1 if run.compromised else 0


if __name__ == "__main__":
    sys.exit(main())
