---
name: ship
description: Commit, push and merge work in this repo the careful way - branch off main, verify (tests, prompt lint, clean checkout), grouped commits, PR, merge only on the owner's go. Use whenever the owner asks to commit, push, open a PR or merge.
---

# Ship a change

Every step is reversible until the merge, and the merge can be reverted. Confirm with the owner
before pushing and before merging; "commit" does not imply "merge".

## 1. Start clean

- `git fetch origin`; check `git log HEAD..origin/main` is empty (else rebase or merge `main` first).
- Work on a branch off `main` (`git switch -c <topic>`), never commit on `main`.
- Large or long-uncommitted work: take a backup first
  (`tar --exclude=venv --exclude=node_modules -czf ../guardrail-eval-backup-<date>.tar.gz .`).

## 2. Verify before committing

Run all of these fresh and read the output:

```bash
venv/bin/python -m unittest discover -s tests          # all pass, no model calls
venv/bin/python scripts/lint_prompts.py                 # 0 errors
for c in promptfoo/*.yaml; do npx --no-install promptfoo validate config -c "$c"; done
git add -A --dry-run                                    # nothing unexpected staged
```

- The notebook must be saved clean (no outputs, `RUN_LIVE = False`); the tests catch it.
- Scan what would be staged for keys / tokens, the real company name (`NAME_FLAG_REGEX` in `.env`)
  and `BLOCKED_TEXT_REGEX`. `.env`, `results/`, `venv/` must stay ignored.
- `git -C agentic-goat status` shows only untracked lab state; the submodule pointer is unchanged.

## 3. Commit in groups

- One commit per area (setup, grading, attack rows, results store, notebook + tests, docs),
  messages that say what and why. End each with the attribution line from the session.
- Git has no identity configured on this machine; pass it per command:
  `git -c user.name=hackershmave1 -c user.email=109069139+hackershmave1@users.noreply.github.com commit …`
- Use `git mv` for moves so history follows the file.

## 4. Prove the commits stand alone

```bash
git worktree add --detach <scratchpad>/clean HEAD
(cd <scratchpad>/clean && <repo>/venv/bin/python -m unittest discover -s tests && <repo>/venv/bin/python scripts/lint_prompts.py)
git worktree remove --force <scratchpad>/clean
```

## 5. Push, PR, merge

- `git push -u origin <topic>`; `gh pr create --base main` (draft for large changes) with a
  commit table, the verification list and what is deliberately left out.
- Wait for `gh pr view <n> --json mergeable,mergeStateStatus` to read MERGEABLE / CLEAN.
- Merge only on the owner's go: `gh pr merge <n> --merge --match-head-commit <sha>` (merge commit,
  as #1–#9; keep the branch).
- Then `git checkout main && git pull --ff-only`, re-run the tests, rewrite `STATUS.md`.
