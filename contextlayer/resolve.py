"""
resolve.py -- build the cross-source links.

Links only ever connect identity keys that already exist as observations, and
every key embeds its tenant and the scope in which its identifier is meaningful.
So a link cannot cross tenants, cannot cross accounts/regions/clusters, and
cannot invent an endpoint. Terraform<->cloud identity needs no link: those
records share an identity key and are grouped as one entity.
"""

from __future__ import annotations

from .model import Link, Strength, k8s_key, rds_key, ec2_key, parse_arn


def build_links(observations) -> list:
    keys = {o.identity_key for o in observations}
    # index k8s objects by (tenant, cluster, kind_lower, ns, name) -> key
    k8s_index = {}
    for o in observations:
        if o.family == "kubernetes":
            kind = o.kind.split("_", 1)[1]
            k8s_index[(o.tenant_id, o.attrs.get("cluster"), kind,
                       o.attrs.get("namespace"), o.native_id.split("/")[-1])] = o.identity_key

    links = []

    for o in observations:
        # Node -> EC2 (providerID, exact)
        if o.kind == "k8s_node" and o.attrs.get("ec2_ref") in keys:
            links.append(Link(o.identity_key, o.attrs["ec2_ref"], Strength.EXACT,
                              "k8s Node.providerID embeds the EC2 instance id", "runtime_placement"))

        # Pod -> Node (nodeName, exact)
        if o.kind == "k8s_pod" and o.attrs.get("node_name"):
            node_key = k8s_index.get((o.tenant_id, o.attrs.get("cluster"), "node", "", o.attrs["node_name"]))
            if node_key:
                links.append(Link(o.identity_key, node_key, Strength.EXACT,
                                  "k8s Pod.spec.nodeName == Node name", "scheduling"))

        # ownerReferences: Pod -> ReplicaSet -> Deployment (exact)
        for owner in o.attrs.get("owner_refs", []):
            owner_key = k8s_index.get((o.tenant_id, o.attrs.get("cluster"),
                                       owner["kind"].lower(), o.attrs.get("namespace"), owner["name"]))
            if owner_key:
                links.append(Link(o.identity_key, owner_key, Strength.EXACT,
                                  f"k8s ownerReference {o.kind.split('_')[1]} -> {owner['kind']}",
                                  "ownership"))

        # catalog app -> Deployment (declared workload, strong)
        if o.kind == "catalog_app":
            w = o.attrs.get("workload", {})
            if w.get("cluster_id") and w.get("deployment_name"):
                dep_key = k8s_key(o.tenant_id, w["cluster_id"], "Deployment",
                                  w.get("namespace") or "", w["deployment_name"])
                if dep_key in keys:
                    links.append(Link(o.identity_key, dep_key, Strength.STRONG,
                                      "catalog declared workload (cluster/namespace/deployment)",
                                      "declared_workload"))

    # dedupe: keep strongest per (src,dst,kind)
    best = {}
    for l in links:
        k = (l.src, l.dst, l.kind)
        if k not in best or l.strength > best[k].strength:
            best[k] = l

    return sorted(best.values(), key=lambda l: (l.src, l.dst, l.kind))


def resolve_dependency_target(app_obs, keys):
    """Resolve a catalog declared_dependency_arn to a cloud identity key (tenant
    scoped). Returns (key, present_bool) or (None, False)."""
    arn = app_obs.attrs.get("declared_dependency_arn")
    if not arn:
        return None, False
    a = parse_arn(arn)
    if a["service"] == "rds":
        key = rds_key(app_obs.tenant_id, a["account"], a["region"], arn.rsplit(":", 1)[-1])
    elif a["service"] == "ec2":
        key = ec2_key(app_obs.tenant_id, a["account"], a["region"], arn.rsplit("/", 1)[-1])
    else:
        return None, False
    return key, (key in keys)
