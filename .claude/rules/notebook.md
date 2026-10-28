---
paths:
  - "notebooks/**"
  - "tests/test_operator_lib.py"
---

# Operator notebook

- `notebooks/operator.ipynb` holds thin cells; all logic is in `notebooks/operator_lib.py`. The
  notebook grades nothing: it reads `outcome.py` results and the store's `v_row_run`.
- **Commit it clean:** no execution counts or outputs, `RUN_LIVE = False`, `REPEAT = 3`. Tests
  enforce this. After running it in VS Code, clear outputs before saving, or reload from disk.
- Scratch is the default: `RUNS_DIR` / `RESULTS_DB` point at `results/scratch/` (own `scratch.db`),
  and with `RUNS_DIR` set `run_evals.sh` does not refresh `results/<config>.*`. A real run needs the
  confirm checkbox. Never write `.env`; show secrets and header values as set / not set only.
- Look: red = LANDED only; green = a logged block only; verdict lines are fixed phrases. VS Code
  ignores `<style>`, so styles are inline `!important`; the flow is HTML, not Mermaid.
- Headless check (scratch only): `OPERATOR_AUTORUN=tools:CTRL-delete venv/bin/jupyter nbconvert --execute …`.
