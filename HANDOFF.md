# Handoff

## Run and test

```bash
# from the repository root (this directory)
python3 run.py                 # deterministic rebuild -> answers/ + printed summary
python3 tests.py               # 6 focused tests (also runnable with: pytest tests.py)
python3 data/tools/check_inputs.py   # provided read-only input-envelope check
```

`run.py data` is the default; pass another root to rebuild from a different copy
of the files. Python 3.10+ , standard library only, no dependencies.

## Layout

```
run.py                     rebuild + emit answers/ + issues
tests.py                   6 tests, each naming the error it catches
DESIGN.md                  design note
contextlayer/
  model.py                 types, identity keys, freshness, widest-path
  ingest.py                files -> tenant-scoped observations
  resolve.py               observations -> tenant-safe links
  build.py                 deterministic World build
  questions.py             Question A and Question B (query the model)
  issues.py                issues report
answers/answers.json       generated Question A + B
answers/issues.json        generated issues report
data/                      the supplied inputs (+ tools/check_inputs.py)
```

## Generated answers (summary; full JSON in `answers/`)

Evaluation time is the manifest's `as_of` = `2026-09-16T12:00:00Z`.
Query defaults: tenant `acme`, environment `prod`, application `payments-api`.

**Question A — running prod EC2 and Terraform managed-binding evidence (tenant acme)**

| Instance | Running | Binding status | Note |
|---|---|---|---|
| i-…101 | yes | managed_binding_found | evidence is **stale** (state observed 2026-09-02, 14 days) |
| i-…102 | yes | managed_binding_found | stale |
| i-…103 | yes | no managed binding — **data reference only** | only read by `data.aws_instance.lookup` |
| i-…104 | yes | no binding found in supplied scope | not in workspace `acme-prod-core` |
| i-…105 | yes | no binding found in supplied scope | |

`i-…106` (stopped) is excluded. A "binding found" establishes management *as of
the state's observation time*, not current management, because the prod state is
stale. For tenant `bravo`, the same question returns
`management_evidence_unavailable` (Terraform not provided) — not "no binding".

**Question B — application context for `payments-api` (acme/prod)**

- Application team (catalog owner): **team-payments**.
- Declared dependency: `payments-db` in account **111…111**, present and fresh;
  resolved by ARN, scoped to the account (not the account-222 `payments-db`).
- Runtime path (overall **strong** — app→deployment is a catalog declaration,
  the rest exact): `payments-api` → Deployment `payments-api` → Pods →
  Nodes `ip-10-0-4-118` / `ip-10-0-4-119` → **EC2 i-…101 and i-…102**.
- The EC2 instance named `payments-api` (i-…103) is deliberately **not** on the
  path — identity is never taken from a Name tag.
- Operator evidence: i-…101 has **conflicting** `operator_team` (AWS tag
  `team-platform` vs stale Terraform tag `team-legacy-platform`); i-…102 agrees.
- Shared infrastructure: node `ip-10-0-4-118` also hosts a `billing-api` pod, so
  instance-level responsibility is not app-specific.

**Issues report** (11): stale prod state; two unavailable `bravo` collections;
cross-tenant id collision on `i-…101`; conflicting `operator_team`; a managed
resource (`i-…099`) with no observed instance; the `payments-api` Name-tag
lookalike; `reports-api` declared but not deployed; and duplicate name/IP pairs.
Each entry states what evidence would resolve it.

## Notes

- Not implemented, by choice: query-time scope enforcement (out-of-scope vs
  in-scope negatives are stated in `limitations`, not yet a distinct verdict);
  reverse dependency lookups; conflict handling beyond `operator_team`.
- `data/README.md` and `data/EXTRACT_NOTES.md` are the supplied brief and
  contract, kept alongside the inputs for reference.
```
