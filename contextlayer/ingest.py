"""
ingest.py -- turn the supplied files into Collections and Observations.

Each payload group is bound to its OWN manifest collection via snapshot_id, so a
single file that holds several tenants (aws_inventory.json, service_catalog.csv)
never has one tenant context smeared across it. Unavailable collections
(status=not_provided) contribute a Collection with no observations.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import timedelta

from .model import (Collection, Observation, parse_ts, region_from_az, parse_arn,
                    ec2_key, rds_key, k8s_key, app_key)


def _tags_to_dict(tag_array):
    return {t["Key"]: t["Value"] for t in (tag_array or [])}


def load_collections(manifest: dict) -> dict:
    out = {}
    for s in manifest["snapshots"]:
        budget = s.get("freshness_budget_seconds")
        out[s["snapshot_id"]] = Collection(
            snapshot_id=s["snapshot_id"],
            family=s["source_family"],
            source_instance_id=s["source_instance_id"],
            tenant_id=s["tenant_id"],
            payload_path=s.get("payload_path"),
            observed_at=parse_ts(s.get("observed_at")),
            exported_at=parse_ts(s.get("exported_at")),
            freshness_budget=timedelta(seconds=budget) if budget is not None else None,
            status=s["status"],
            coverage=s["coverage"],
            scope=s.get("scope", {}))
    return out


def ingest(root: str, collections: dict) -> list:
    obs = []
    by_family = {}
    for c in collections.values():
        by_family.setdefault(c.family, []).append(c)

    obs += _ingest_aws(root, collections)
    obs += _ingest_terraform(root, collections)
    obs += _ingest_k8s(root, collections)
    obs += _ingest_catalog(root, collections)
    return obs


# ---- AWS -------------------------------------------------------------------
def _ingest_aws(root, collections):
    aws = [c for c in collections.values() if c.family == "aws" and c.available]
    if not aws:
        return []
    path = aws[0].payload_path
    with open(os.path.join(root, path)) as f:
        payload = json.load(f)
    by_sid = {c.snapshot_id: c for c in aws}
    out = []
    for group in payload["collections"]:
        c = by_sid.get(group["snapshot_id"])
        if not c:
            continue
        acct, region = c.scope.get("account_id"), c.scope.get("region")
        for i, r in enumerate(group.get("instances", [])):
            iid = r["InstanceId"]
            out.append(Observation(
                c.snapshot_id, c.tenant_id, "aws", "ec2_instance", iid,
                ec2_key(c.tenant_id, acct, region, iid), c.observed_at,
                {"state": r.get("State", {}).get("Name"),
                 "private_ip": r.get("PrivateIpAddress"), "vpc": r.get("VpcId"),
                 "account": acct, "region": region, "environment": c.scope.get("environment"),
                 "tags": _tags_to_dict(r.get("Tags"))},
                f"{path}#{group['snapshot_id']}/instances[{i}]/{iid}"))
        for i, r in enumerate(group.get("databases", [])):
            arn = parse_arn(r["DBInstanceArn"])
            did = r["DBInstanceIdentifier"]
            out.append(Observation(
                c.snapshot_id, c.tenant_id, "aws", "rds_db", did,
                rds_key(c.tenant_id, arn["account"], arn["region"], did), c.observed_at,
                {"status": r.get("DBInstanceStatus"), "arn": r["DBInstanceArn"],
                 "account": arn["account"], "region": arn["region"],
                 "tags": _tags_to_dict(r.get("Tags"))},
                f"{path}#{group['snapshot_id']}/databases[{i}]/{did}"))
    return out


# ---- Terraform -------------------------------------------------------------
def _ingest_terraform(root, collections):
    out = []
    for c in collections.values():
        if c.family != "terraform" or not c.available:
            continue
        with open(os.path.join(root, c.payload_path)) as f:
            state = json.load(f)
        for i, r in enumerate(state.get("resources", [])):
            vals = r.get("values", {})
            arn = parse_arn(vals["arn"])
            if r["type"] == "aws_instance":
                key = ec2_key(c.tenant_id, arn["account"], arn["region"], vals["id"])
            elif r["type"] == "aws_db_instance":
                key = rds_key(c.tenant_id, arn["account"], arn["region"], vals["id"])
            else:
                continue
            out.append(Observation(
                c.snapshot_id, c.tenant_id, "terraform", "tf_resource", r["address"],
                key, c.observed_at,
                {"mode": r["mode"], "type": r["type"], "provider_id": vals["id"],
                 "arn": vals["arn"], "workspace_id": c.scope.get("workspace_id"),
                 "tf_tags": vals.get("tags", {})},
                f"{c.payload_path}#{c.snapshot_id}/resources[{i}]/{r['address']}"))
    return out


# ---- Kubernetes ------------------------------------------------------------
def _ingest_k8s(root, collections):
    k = [c for c in collections.values() if c.family == "kubernetes" and c.available]
    if not k:
        return []
    path = k[0].payload_path
    with open(os.path.join(root, path)) as f:
        payload = json.load(f)
    by_sid = {c.snapshot_id: c for c in k}
    out = []
    for group in payload["collections"]:
        c = by_sid.get(group["snapshot_id"])
        if not c:
            continue
        cluster = c.scope.get("cluster_id")
        acct, region = c.scope.get("account_id"), c.scope.get("region")
        for i, item in enumerate(group.get("items", [])):
            md = item.get("metadata", {})
            kind = item["kind"]
            ns = md.get("namespace", "")
            name = md["name"]
            attrs = {"uid": md.get("uid"), "namespace": ns, "cluster": cluster,
                     "labels": md.get("labels", {}),
                     "owner_refs": md.get("ownerReferences", [])}
            if kind == "Pod":
                attrs["node_name"] = item.get("spec", {}).get("nodeName")
                attrs["phase"] = item.get("status", {}).get("phase")
            if kind == "Deployment":
                attrs["replicas"] = item.get("spec", {}).get("replicas")
            if kind == "Node":
                pid = item.get("spec", {}).get("providerID")
                attrs["provider_id"] = pid
                if pid:  # aws:///<az>/<instance-id>
                    az, iid = pid.rsplit("/", 1)[0].rsplit("/", 1)[-1], pid.rsplit("/", 1)[-1]
                    attrs["ec2_ref"] = ec2_key(c.tenant_id, acct, region_from_az(az), iid)
            out.append(Observation(
                c.snapshot_id, c.tenant_id, "kubernetes", f"k8s_{kind.lower()}",
                f"{ns}/{name}" if ns else name,
                k8s_key(c.tenant_id, cluster, kind, ns, name), c.observed_at, attrs,
                f"{path}#{group['snapshot_id']}/items[{i}]/{kind}/{name}"))
    return out


# ---- Catalog ---------------------------------------------------------------
def _ingest_catalog(root, collections):
    cat = [c for c in collections.values() if c.family == "catalog" and c.available]
    if not cat:
        return []
    path = cat[0].payload_path
    by_sid = {c.snapshot_id: c for c in cat}
    out = []
    with open(os.path.join(root, path), newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            c = by_sid.get(row["snapshot_id"])
            if not c:
                continue
            out.append(Observation(
                c.snapshot_id, c.tenant_id, "catalog", "catalog_app", row["service_id"],
                app_key(c.tenant_id, row["service_id"]), c.observed_at,
                {"service_name": row["service_name"], "environment": row["environment"],
                 "owner_team": row["owner_team"] or None,
                 "workload": {"cluster_id": row["cluster_id"] or None,
                              "namespace": row["namespace"] or None,
                              "deployment_name": row["deployment_name"] or None},
                 "declared_dependency_arn": row["declared_dependency_arn"] or None},
                f"{path}#{row['snapshot_id']}/row[{i}]/{row['service_id']}"))
    return out
