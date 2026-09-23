"""
issues.py -- a small, machine-readable issues report over the whole World.
Each issue says what additional evidence would resolve it.
"""

from __future__ import annotations

from .questions import _index, _key_label


def issues_report(world):
    idx = _index(world)
    issues = []

    # 1. stale collections (available but past budget)
    for c in sorted(world.collections.values(), key=lambda x: x.snapshot_id):
        if c.available and c.is_stale(world.as_of):
            issues.append({
                "type": "stale_collection", "severity": "high",
                "detail": f"{c.snapshot_id} ({c.family}) observed {c.observed_at.isoformat()} is past its "
                          f"freshness budget at as_of; its values describe the past, not necessarily now.",
                "evidence": [c.payload_path],
                "resolved_by": "a fresher export of this collection within its freshness budget."})

    # 2. unavailable collections (not empty)
    for c in sorted(world.collections.values(), key=lambda x: x.snapshot_id):
        if not c.available:
            issues.append({
                "type": "unavailable_collection", "severity": "medium",
                "detail": f"{c.snapshot_id} ({c.family}, tenant {c.tenant_id}) was not provided; questions "
                          f"needing it must answer UNKNOWN, not 'none'.",
                "evidence": [], "resolved_by": "supplying this collection's payload."})

    # 3. cross-tenant provider-id collision (same instance id + account, different tenants)
    by_id_acct = {}
    for o in world.obs(kind="ec2_instance"):
        by_id_acct.setdefault((o.native_id, o.attrs.get("account")), set()).add(o.tenant_id)
    for (iid, acct), tenants in sorted(by_id_acct.items()):
        if len(tenants) > 1:
            issues.append({
                "type": "cross_tenant_identifier_collision", "severity": "medium",
                "detail": f"EC2 {iid} appears under tenants {sorted(tenants)} in the same AWS account {acct}; "
                          f"kept as distinct entities because tenant context is authoritative from the manifest.",
                "evidence": [o.locator for o in world.obs(kind="ec2_instance")
                             if o.native_id == iid and o.attrs.get("account") == acct],
                "resolved_by": "confirmation that the shared account is intentional; provider account is not the tenant."})

    # 4. operator_team conflict per cloud entity (aws tag vs managed tf tag)
    for key, group in sorted(idx.items()):
        if not key.startswith("ec2|"):
            continue
        aws_o = next((x for x in group if x.family == "aws"), None)
        tf_m = [x for x in group if x.family == "terraform" and x.attrs.get("mode") == "managed"]
        teams = {}
        if aws_o and aws_o.attrs.get("tags", {}).get("operator_team"):
            teams[aws_o.attrs["tags"]["operator_team"]] = aws_o.locator
        for t in tf_m:
            ot = t.attrs.get("tf_tags", {}).get("operator_team")
            if ot:
                teams.setdefault(ot, t.locator)
        if len(teams) > 1:
            issues.append({
                "type": "conflicting_operator_team", "severity": "medium",
                "detail": f"{_key_label(key)} has disagreeing operator_team statements: {sorted(teams)}.",
                "evidence": sorted(teams.values()),
                "resolved_by": "an authoritative operator source, or a fresh Terraform state if the tag simply lagged."})

    # 5. managed TF resource with no observed inventory instance
    for key, group in sorted(idx.items()):
        tf_m = [x for x in group if x.family == "terraform" and x.attrs.get("mode") == "managed"]
        has_aws = any(x.family == "aws" for x in group)
        if tf_m and not has_aws:
            issues.append({
                "type": "managed_without_inventory", "severity": "medium",
                "detail": f"{_key_label(key)} is Terraform-managed but no matching instance is in the supplied "
                          f"inventory scope; it may be terminated/replaced (the state is stale) or out of scope.",
                "evidence": [t.locator for t in tf_m],
                "resolved_by": "a fresh inventory including terminated instances, or a fresh state."})

    # 6. Name tag equal to a catalog service_name (misleading similarity)
    service_names = {o.attrs.get("service_name") for o in world.obs(kind="catalog_app")}
    for o in world.obs(kind="ec2_instance"):
        nm = o.attrs.get("tags", {}).get("Name")
        if nm in service_names:
            issues.append({
                "type": "misleading_name_similarity", "severity": "low",
                "detail": f"EC2 {o.native_id} has Name tag '{nm}' matching a catalog service; a Name tag is a label, "
                          f"not identity, and is not used to link the instance to that application.",
                "evidence": [o.locator],
                "resolved_by": "n/a (correct behaviour); identity is resolved via workload->node->providerID."})

    # 7. declared workload not observed
    dep_keys = {o.identity_key for o in world.observations if o.kind == "k8s_deployment"}
    for o in world.obs(kind="catalog_app"):
        w = o.attrs.get("workload", {})
        if not (w.get("cluster_id") and w.get("deployment_name")):
            continue
        from .model import k8s_key
        want = k8s_key(o.tenant_id, w["cluster_id"], "Deployment", w.get("namespace") or "", w["deployment_name"])
        if want in dep_keys:
            continue
        # is the relevant k8s collection available?
        kcoll = [c for c in world.collections.values()
                 if c.family == "kubernetes" and c.tenant_id == o.tenant_id
                 and c.scope.get("cluster_id") == w["cluster_id"]]
        available = any(c.available for c in kcoll)
        issues.append({
            "type": "declared_workload_not_observed",
            "severity": "medium" if available else "low",
            "detail": f"Catalog app {o.native_id} declares deployment "
                      f"{w['cluster_id']}/{w['namespace']}/{w['deployment_name']}, "
                      + ("which is absent from a complete, available k8s collection (likely truly not deployed)."
                         if available else "but that cluster's k8s collection is unavailable (UNKNOWN, not absent)."),
            "evidence": [o.locator],
            "resolved_by": "n/a if intentionally undeployed; otherwise the k8s collection for that cluster."})

    # 8. duplicate Name/IP among distinct instances (same tenant)
    seen = {}
    for o in world.obs(kind="ec2_instance"):
        sig = (o.tenant_id, o.attrs.get("tags", {}).get("Name"), o.attrs.get("private_ip"))
        seen.setdefault(sig, []).append(o)
    for sig, group in sorted(seen.items(), key=lambda kv: str(kv[0])):
        if len(group) > 1:
            issues.append({
                "type": "duplicate_name_or_ip", "severity": "low",
                "detail": f"Instances {sorted(g.native_id for g in group)} share Name '{sig[1]}' and IP {sig[2]} "
                          f"but are distinct (different InstanceId/VPC); name and IP are not identity.",
                "evidence": [g.locator for g in group],
                "resolved_by": "n/a (correct behaviour); identity is the InstanceId within account/region."})

    issues.sort(key=lambda i: (i["type"], i["detail"]))
    return {"as_of": world.as_of.isoformat(), "count": len(issues), "issues": issues}
