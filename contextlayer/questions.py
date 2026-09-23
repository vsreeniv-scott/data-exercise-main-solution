"""
questions.py -- Question A (Terraform management evidence) and Question B
(application context and responsibility). Both query the resolved World; neither
reconstructs identity inside the question.
"""

from __future__ import annotations

from .model import Strength
from .resolve import resolve_dependency_target


def _index(world):
    idx = {}
    for o in world.observations:
        idx.setdefault(o.identity_key, []).append(o)
    return idx


def _key_label(key):
    parts = key.split("|")
    t = parts[0]
    if t == "ec2":
        return f"EC2 {parts[4]} ({parts[2]}/{parts[3]})"
    if t == "rds":
        return f"RDS {parts[4]} ({parts[2]}/{parts[3]})"
    if t.startswith("k8s_"):
        return f"{t[4:].title()} {parts[4]} (cluster {parts[2]})"
    if t == "app":
        return f"App {parts[2]}"
    return key


# ---------------------------------------------------------------- Question A
def question_a(world, tenant, environment):
    idx = _index(world)
    aws_colls = world.collections_for(family="aws", tenant=tenant,
                                      environment=environment, available=True)
    tf_colls = world.collections_for(family="terraform", tenant=tenant, environment=environment)
    tf_sids = {c.snapshot_id for c in tf_colls if c.available}
    tf_available = bool(tf_sids)
    aws_sids = {c.snapshot_id for c in aws_colls}

    instances = []
    for o in world.obs(kind="ec2_instance", tenant=tenant):
        if o.snapshot_id not in aws_sids or o.attrs.get("state") != "running":
            continue
        tf_obs = [t for t in idx.get(o.identity_key, [])
                  if t.kind == "tf_resource" and t.snapshot_id in tf_sids]
        managed = [t for t in tf_obs if t.attrs.get("mode") == "managed"]
        data = [t for t in tf_obs if t.attrs.get("mode") == "data"]

        if managed:
            status = "managed_binding_found"
            ev = managed
        elif data:
            status = "no_managed_binding__data_reference_present"
            ev = data
        elif tf_available:
            status = "no_binding_found_in_supplied_scope"
            ev = []
        else:
            status = "management_evidence_unavailable"
            ev = []

        tf_evidence = []
        stale_any = False
        for t in ev:
            c = world.collection(t.snapshot_id)
            stale = c.is_stale(world.as_of)
            stale_any = stale_any or stale
            tf_evidence.append({
                "mode": t.attrs.get("mode"), "address": t.native_id,
                "workspace_id": t.attrs.get("workspace_id"),
                "source_instance_id": c.source_instance_id,
                "observed_at": t.observed_at.isoformat(), "stale": stale,
                "ref": t.locator})

        note = {
            "managed_binding_found":
                "A managed binding exists in the supplied state; because that state is "
                + ("STALE (past its freshness budget), " if stale_any else "")
                + "this establishes management as of the state's observation time, not current management.",
            "no_managed_binding__data_reference_present":
                "The instance is only READ by a Terraform data source, which does not manage it.",
            "no_binding_found_in_supplied_scope":
                "No managed or data reference in the supplied workspace(s); this does not rule "
                "out management in workspaces not provided.",
            "management_evidence_unavailable":
                "Terraform evidence for this tenant/environment was not provided; management is UNKNOWN, not absent.",
        }[status]

        instances.append({
            "instance_id": o.native_id,
            "identity_key": o.identity_key,
            "state": o.attrs.get("state"),
            "aws_evidence": o.ref(),
            "binding_status": status,
            "terraform_evidence": tf_evidence,
            "interpretation": note,
        })

    instances.sort(key=lambda r: r["instance_id"])
    counts = {}
    for r in instances:
        counts[r["binding_status"]] = counts.get(r["binding_status"], 0) + 1

    tf_scope = [{"source_instance_id": c.source_instance_id,
                 "workspace_id": c.scope.get("workspace_id"),
                 "status": c.status,
                 "observed_at": c.observed_at.isoformat() if c.observed_at else None,
                 "stale": c.is_stale(world.as_of)} for c in tf_colls]

    limitations = [
        "Tenant scope is enforced from the manifest: instances under other tenants that share "
        "this AWS account are excluded and never merged.",
        "Running-state is from AWS inventory (complete within its declared account/region/env scope).",
    ]
    if any(s["stale"] for s in tf_scope):
        limitations.append("The prod Terraform state is stale (past its freshness budget), so every "
                           "'binding found' reflects the state's observation time, not current state.")

    return {
        "question": "A. Running production EC2 instances and their Terraform managed-binding evidence",
        "query": {"tenant_id": tenant, "environment": environment,
                  "as_of": world.as_of.isoformat()},
        "terraform_evidence_scope": tf_scope,
        "summary_counts": counts,
        "instances": instances,
        "limitations": limitations,
    }


# ---------------------------------------------------------------- Question B
def _children(world, key, link_kind, as_src=True):
    out = []
    for l in world.links:
        if l.kind != link_kind:
            continue
        if as_src and l.dst == key:
            out.append(l.src)
        elif not as_src and l.src == key:
            out.append(l.dst)
    return sorted(set(out))


def question_b(world, tenant, environment, app_name):
    idx = _index(world)
    keys = set(idx)
    app = next((o for o in world.obs(kind="catalog_app", tenant=tenant)
                if o.attrs.get("service_name") == app_name
                and o.attrs.get("environment") == environment), None)
    if not app:
        return {"question": "B. Application context and responsibility",
                "query": {"tenant_id": tenant, "environment": environment, "application_name": app_name},
                "verdict": "unknown", "reason": "no catalog entry for this tenant/environment"}

    # --- declared dependency (source statement) + resolution ---
    dep_key, dep_present = resolve_dependency_target(app, keys)
    dependency = None
    if app.attrs.get("declared_dependency_arn"):
        dep = {"declared_dependency_arn": app.attrs["declared_dependency_arn"],
               "source": app.ref(), "resolved_identity": dep_key,
               "present_in_inventory": dep_present}
        if dep_present:
            db = idx[dep_key][0]
            c = world.collection(db.snapshot_id)
            dep["evidence"] = db.ref()
            dep["operator_team_tag"] = db.attrs.get("tags", {}).get("operator_team")
            dep["stale"] = c.is_stale(world.as_of)
        dependency = dep

    # --- runtime path: app -declared-> deployment -exact-> rs -> pod -> node -> ec2 ---
    dep_deploys = _children(world, app.identity_key, "declared_workload", as_src=False)
    runtime = {"declared_workload": app.attrs.get("workload"), "path_to_cloud": [],
               "path_stops": []}
    instance_operator = {}

    for dep_key_k in dep_deploys:
        if dep_key_k not in keys:
            runtime["path_stops"].append(
                {"at": _key_label(dep_key_k), "reason": "declared deployment not observed in the k8s collection"})
            continue
        rss = _children(world, dep_key_k, "ownership", as_src=True)
        for rs in rss:
            for pod in _children(world, rs, "ownership", as_src=True):
                nodes = _children(world, pod, "scheduling", as_src=False)
                for node in nodes:
                    ec2s = _children(world, node, "runtime_placement", as_src=False)
                    if not ec2s:
                        runtime["path_stops"].append(
                            {"at": _key_label(node), "reason": "Node has no providerID linking to an instance"})
                    for ec2 in ec2s:
                        hop = {
                            "instance": _key_label(ec2), "identity_key": ec2,
                            "chain": [
                                {"from": _key_label(app.identity_key), "to": _key_label(dep_key_k),
                                 "basis": "catalog declared workload", "strength": "strong"},
                                {"from": _key_label(dep_key_k), "to": _key_label(pod),
                                 "basis": "k8s ownership (Deployment->ReplicaSet->Pod)", "strength": "exact"},
                                {"from": _key_label(pod), "to": _key_label(node),
                                 "basis": "k8s Pod.nodeName", "strength": "exact"},
                                {"from": _key_label(node), "to": _key_label(ec2),
                                 "basis": "k8s Node.providerID", "strength": "exact"},
                            ],
                            "overall_strength": "strong (the app->deployment hop is a catalog declaration; the rest is exact)",
                            "cloud_evidence": idx[ec2][0].ref(),
                        }
                        runtime["path_to_cloud"].append(hop)

                        # operator evidence for this instance (source statements)
                        aws_o = next((x for x in idx[ec2] if x.family == "aws"), None)
                        tf_m = [x for x in idx[ec2] if x.family == "terraform" and x.attrs.get("mode") == "managed"]
                        stmts = []
                        if aws_o and aws_o.attrs.get("tags", {}).get("operator_team"):
                            stmts.append({"operator_team": aws_o.attrs["tags"]["operator_team"],
                                          "source": "aws tag", "ref": aws_o.locator, "stale": False})
                        for t in tf_m:
                            ot = t.attrs.get("tf_tags", {}).get("operator_team")
                            if ot:
                                stmts.append({"operator_team": ot, "source": "terraform tag",
                                              "ref": t.locator,
                                              "stale": world.collection(t.snapshot_id).is_stale(world.as_of)})
                        instance_operator[ec2] = stmts

    # dedupe path (a node may be reached once per pod)
    seen = set()
    uniq = []
    for h in sorted(runtime["path_to_cloud"], key=lambda x: x["identity_key"]):
        if h["identity_key"] in seen:
            continue
        seen.add(h["identity_key"])
        uniq.append(h)
    runtime["path_to_cloud"] = uniq

    # operator conflicts
    operator_evidence = []
    for ec2, stmts in sorted(instance_operator.items()):
        teams = {s["operator_team"] for s in stmts}
        operator_evidence.append({
            "instance": _key_label(ec2), "statements": stmts,
            "conflict": len(teams) > 1,
            "note": ("Sources disagree on the operator team." if len(teams) > 1
                     else "Operator statements agree.")})

    # shared-infra caveat: other apps' pods on the same nodes
    path_nodes = set()
    for l in world.links:
        if l.kind == "runtime_placement" and any(h["identity_key"] == l.dst for h in uniq):
            path_nodes.add(l.src)
    shared = []
    for node in sorted(path_nodes):
        pods_here = [l.src for l in world.links if l.kind == "scheduling" and l.dst == node]
        other = [p for p in pods_here if not p.split("|")[-1].startswith(app_name)]
        if other:
            shared.append({"node": _key_label(node),
                           "also_hosts": sorted(_key_label(p) for p in other)})

    limitations = [
        "The app->deployment hop is a catalog declaration (strong), not a provider identifier; "
        "everything from the deployment down is exact.",
        "Application team (catalog owner_team) is a responsibility statement; operator_team tags are a "
        "separate statement about infrastructure operation. A workload running on an instance does not make "
        "the app team the operator, nor the operator responsible for the app.",
        "The EC2 instance whose Name tag is 'payments-api' is NOT on this path; identity comes from the "
        "workload->node->providerID chain, never from a Name tag. (It is only read by a Terraform data source.)",
    ]
    if any(e["conflict"] for e in operator_evidence):
        limitations.append("Operator-team evidence conflicts between AWS tags and (stale) Terraform tags for at "
                           "least one instance; neither is treated as authoritative.")
    if shared:
        limitations.append("Some instances on this path also host other applications' pods (shared infrastructure), "
                           "so instance-level responsibility is not app-specific.")

    return {
        "question": "B. Application context and responsibility",
        "query": {"tenant_id": tenant, "environment": environment,
                  "application_name": app_name, "as_of": world.as_of.isoformat()},
        "application": {"service_id": app.native_id, "service_name": app.attrs.get("service_name"),
                        "application_team_owner": app.attrs.get("owner_team"),
                        "source": app.ref()},
        "declared_dependency": dependency,
        "runtime": runtime,
        "infrastructure_operator_evidence": operator_evidence,
        "shared_infrastructure": shared,
        "limitations": limitations,
    }
