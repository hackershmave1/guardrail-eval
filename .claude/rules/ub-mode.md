---
paths:
  - "promptfoo/**/*.py"
  - "scripts/**/*.py"
  - "notebooks/operator_lib.py"
  - "tests/**"
  - ".claude/hooks/**"
  - ".claude/skills/ub-mode/**"
---

# ub-mode in this repo

`.claude/skills/ub-mode/SKILL.md` is Uncle Bob's upstream file, unedited
(`github.com/unclebob/ub-mode` at `57a8bdd`). Replace it whole to update; the repo-specific
parts live here.

## Tools

crapper, dryer and mutator live in `~/tools`, one venv `~/tools/ub-venv`, on PATH via
`~/.local/bin`. They are not in `requirements.txt`. Reinstall:

```bash
cd ~/tools && for r in crapper dryer mutator; do git clone https://github.com/unclebob/$r; done
python3 -m venv ub-venv && ub-venv/bin/pip install -e crapper -e dryer -e mutator
ln -sf ~/tools/ub-venv/bin/{crapper,dryer,mutator} ~/.local/bin/
```

Always name the paths: the tools skip `venv/` but not the `agentic-goat/` submodule.

```bash
P="promptfoo scripts notebooks .claude/hooks"
crapper --threshold 10 $P                # CRAP per function; exit 2 when any is over 10
dryer --threshold 0.8 $P                 # the default is 0.82; the skill says 0.8
mutator promptfoo/providers/outcome.py   # one file at a time, or: mutator --changed
```

- crapper takes about a minute (it runs the suite under coverage). Output goes to `.metrics/` and
  `target/`, both gitignored.
- Baselines when ub-mode came in (2026-11-01): 110 of 264 functions over CRAP 10; mutator on
  `outcome.py` 14 killed, 4 survived, 2 uncovered (77.8%). Hold the code you change to the gates.

## Keep the tools honest

- The tools run `unittest discover` from the root; `tests/__init__.py` is what lets it find the
  suite. Without it crapper scores everything 0% and mutator's baseline fails.
- mutator runs each mutant in a symlink overlay. Build `ROOT` and every `sys.path.insert` from
  `Path(__file__).absolute()`, never `.resolve()`: `resolve()` follows the symlink back to the
  real repo, the tests import the unmutated file, and every mutant falsely survives.
- A mutator run where every mutant survives is a broken harness until proved otherwise.

## Commits

Under ub-mode the owner wants Uncle Bob's rule (2026-11-01): commit often, in small ordered
commits, each saying why, what changed and how it was proved. Commit on a topic branch off `main`,
never on `main`. "Push to the main line at the end" means the `ship` skill: push the branch, open
a PR, merge only on the owner's go.
