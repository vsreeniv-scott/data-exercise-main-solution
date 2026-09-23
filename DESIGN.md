# Design note

## Model and design

The layer keeps **observations, not facts**. Each record from each file becomes an
`Observation` tagged with its collection, tenant, observation time, and a source
locator. Nothing is flattened into a single canonical row, so provenance and
time survive to the answer.

Identity is a **deterministic key that encodes the scope in which an identifier
is meaningful**: `ec2|tenant|account|region|InstanceId`, `rds|tenant|account|region|id`,
`k8s_<kind>|tenant|cluster|namespace|name`, `app|tenant|service_id`. Two records
describe the same object only when their keys are equal. This is the spine of the
model: it makes the same object seen by AWS inventory and Terraform state *one
entity* (they share a key), while keeping `i-...101` under `acme` and under
`bravo` — same AWS account, same id — as **two** entities, and `payments-db` in
two accounts as two databases.

Relationships that are *not* identity are **links** with a basis and a strength:
k8s ownership (`Pod→ReplicaSet→Deployment`) and placement (`Pod.nodeName`,
`Node.providerID→instance`) are `exact`; the catalog's declared workload and
declared dependency are `strong` (org statements that can lag). A derived
conclusion inherits the weakest link on its path, so the runtime path from an app
to its instances is reported as `strong` overall, because the app→deployment hop
is a declaration even though every hop below it is exact.

I chose observations + typed identity keys + rated links (a small graph) over a
merged inventory table because the exercise's failure mode is *premature
merging*. Keys give deterministic, tenant-safe identity and a duplicate-free
rebuild; links keep "runs on" separate from "manages" separate from "depends on".

**Two rules the implementation preserves.** (1) *Tenant/scope isolation*: links
only connect keys that already exist as observations, and every key embeds its
tenant and provider scope, so no link can cross a tenant or an account/region/
cluster boundary — the manifest's tenant context is authoritative, never the
provider account. (2) *Managed ≠ data ≠ named*: a Terraform `data` reference is
never counted as a managed binding, and an instance's `Name` tag is never used to
establish application identity (only the workload→node→providerID chain is).

## Two consequential decisions

**Identity key includes account+region (and tenant), not just the native id.**
Alternative: key on `InstanceId`/`DBInstanceIdentifier` alone. That is simpler
and would "work" on this data for EC2, but it silently merges `payments-db`
across accounts and fuses tenants on a shared account. I would revisit the choice
if the notes said an id is globally unique within a tenant, or if a real ARN were
always present on EC2 records (it is not; account/region come from the manifest
scope) — then I would key on the ARN directly.

**Absence is only a "no" from a fresh, complete, in-scope collection; otherwise
UNKNOWN.** Question A distinguishes *binding found* / *no binding in supplied
scope* / *data-reference-only* / *evidence unavailable*, and freshness is measured
against the manifest `as_of`. Alternative: report "not managed" whenever no
managed resource is found. That would turn the stale prod state and the missing
`bravo` state into false negatives. I would revisit if a collection carried an
authoritative "this is the complete set of workspaces" signal, which would let a
scoped "no" become a global "no".

## Verification

I doubted my own freshness handling because one timestamp is written with a
`-04:00` offset (`k8s-acme-staging`), and a naive parser or a wall-clock
comparison would misjudge it. I checked by asserting, against the manifest
`as_of`, that `tf-acme-prod` is stale (14 days, 1-day budget), `aws-acme-prod` is
fresh (10 min), and the offset collection is fresh (7 min, not mis-parsed as 4
hours). The offset one initially looked stale in a scratch check using
`datetime.now()`; that confirmed the bug the brief warns about, and I kept the
manifest `as_of` as the only clock. The provided `check_inputs.py` also passes,
confirming I read the envelope correctly before trusting any values.

## Readiness and next steps

A **read-only internal consumer may rely on**: the two answers and the issues
report as machine-readable JSON with per-claim source locators and observation
times; correct tenant isolation on a shared account; the managed/data/name
distinctions; and a duplicate-free deterministic rebuild.

The **highest-risk remaining gap** is scope enforcement at *query* time. Identity
keys are scope-safe, but a caller can still ask about a scope the data doesn't
cover and read a bounded "not found" as global. Today that is stated in
`limitations`, not enforced. The **next two changes** I would make: (1) add an
explicit scope check so an out-of-scope query returns `unknown_outside_scope`
rather than a qualified negative; (2) generalise Question A/B into a small typed
query interface returning a common answer envelope (verdict, scope, evidence,
limitations), so new questions inherit the honesty machinery instead of
re-implementing it.

## Time and unfinished work

Roughly: ~25% reading the notes and mapping the traps; ~45% model, ingest,
resolve, and the two questions; ~20% the issues report and tests; ~10% this note.
Unfinished, by choice: query-time scope enforcement (above); the reversed
dependency direction (who depends *on* payments-db); and richer conflict handling
beyond `operator_team`. These are missing evidence, not design gaps — the model
already carries what they need.
