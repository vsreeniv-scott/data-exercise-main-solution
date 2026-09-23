# Context Layer Exercise

This project builds a small, deterministic context layer that ingests cloud inventory, Terraform state, Kubernetes resources, and a service catalog, then answers operational questions while preserving provenance and uncertainty.

The solution follows the brief’s intent: it does not merge incomplete evidence into certainty, keeps tenant and scope boundaries explicit, and distinguishes between a real binding, a missing binding, and unavailable evidence.

## Why this exists

The exercise asks for a local context layer that can answer two questions without hard-coding results:

1. Which running production EC2 instances have Terraform management evidence, and what is the evidence quality?
2. For the `payments-api` app in production, what are its declared dependencies and runtime path from workloads to cloud resources?

The implementation stores observations with source metadata, builds explicit links between matching entities, and only answers from the supplied evidence plus the trusted manifest context.

## High-level approach

The model uses:

- Observations: each source record is kept as an observation with collection metadata, time, tenant, and source locator.
- Identity keys: records are keyed by tenant + provider scope rather than by raw display names alone.
- Links: cross-source relationships are explicit and carry basis + strength information.
- Query results: questions are answered by querying the world model rather than reconstructing identities manually inside each question.

This prevents common mistakes such as:

- merging tenants that share the same AWS account or instance id,
- treating Terraform data sources as management,
- using a Name tag as application identity,
- flattening stale or unavailable evidence into false negatives.

## Repository layout

- `run.py` — rebuilds the world and writes the answer artifacts
- `tests.py` — focused verification tests for important assumptions
- `contextlayer/` — model, ingest, identity resolution, and question logic
- `answers/` — generated JSON outputs for the required answers and issues report
- `data/` — synthetic input files and manifest metadata

## Inputs

The project consumes the supplied extracts in `data/`:

- `manifest.json`
- `aws_inventory.json`
- `terraform_state/`
- `k8s_resources.json`
- `service_catalog.csv`
- `EXTRACT_NOTES.md`

## Run the solution

From the repository root:

```bash
python3 run.py
```

This rebuilds the world, queries the model, and writes:

- `answers/answers.json`
- `answers/issues.json`

## Run the tests

```bash
python3 tests.py
```

Verified behavior for this repository:

- Fresh run: `python3 tests.py`
- Result: `6 passed`

## Output artifacts

The generated JSON includes:

- Question A: running production EC2 instances and Terraform evidence
- Question B: application dependency and runtime path for `payments-api`
- Issues report: unresolved links, stale collections, conflicts, and limitations

These outputs intentionally preserve provenance, observation time, and scope so that conclusions remain traceable.

## Design principles preserved

1. Tenant and scope isolation
   - Objects are keyed with tenant and provider scope boundaries.
   - Different tenants or accounts do not collapse together.

2. Evidence is never silently converted into certainty
   - Stale and unavailable collections are qualified.
   - Data references are not treated as managed resources.

3. Deterministic rebuilds
   - Rebuilding the same input produces the same identities and links.

## Limitations

This is a bounded exercise and not a production-grade infrastructure platform. The implementation intentionally avoids:

- live cloud access
- runtime authentication
- production connectors
- broad ontology modeling
- general-purpose dependency resolution beyond the supplied contract

The model is designed to be honest about missing evidence rather than guessing.

## Notes

The project is intentionally small and explicit: its value is in the careful handling of uncertainty, provenance, and scope. The design is meant to be easy to inspect, reason about, and extend if a later iteration adds more evidence sources.
