"""
model.py -- core types for a manifest-driven, tenant-scoped context layer.

Principles encoded here (not just in prose):

* Every value is an OBSERVATION carrying its collection, tenant, observation
  time, and a source locator. Nothing is merged into a bare "fact".
* IDENTITY is a deterministic key that includes the tenant and the scope in
  which a provider identifier is meaningful (account+region for cloud, cluster
  for k8s). Two records only describe the same object if their keys are equal.
  This is what stops a shared AWS account or a reused id from fusing two
  tenants' objects, or 'payments-db' in two accounts from becoming one database.
* Cross-source LINKS are explicit edges with a basis and a strength. A derived
  conclusion inherits the weakest link on its path (widest-path search), so a
  declared mapping is never laundered into an exact fact.
* A COLLECTION can be available+complete-in-scope, stale (past its freshness
  budget), or unavailable. Unavailable is not empty; absence under it is UNKNOWN.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Optional


# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #
def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse an offset-aware ISO timestamp (handles trailing Z and +hh:mm)."""
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def human_age(observed_at: Optional[datetime], as_of: datetime) -> str:
    if observed_at is None:
        return "n/a"
    secs = (as_of - observed_at).total_seconds()
    if secs < 3600:
        return f"{int(secs//60)} min"
    if secs < 86400:
        return f"{int(secs//3600)} h"
    d = int(secs // 86400)
    return f"{d} day" if d == 1 else f"{d} days"


# --------------------------------------------------------------------------- #
# strength of an identity bridge
# --------------------------------------------------------------------------- #
class Strength(IntEnum):
    HEURISTIC = 1   # a guess (e.g. name overlap) -- never used for identity here
    STRONG = 2      # a declared/derived mapping that can be stale or wrong
    EXACT = 3       # same provider identifier / structural reference

    def label(self) -> str:
        return self.name.lower()


# --------------------------------------------------------------------------- #
# collection metadata (one manifest snapshot)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Collection:
    snapshot_id: str
    family: str                 # aws | terraform | kubernetes | catalog
    source_instance_id: str
    tenant_id: str
    payload_path: Optional[str]
    observed_at: Optional[datetime]
    exported_at: Optional[datetime]
    freshness_budget: Optional[timedelta]
    status: str                 # success | not_provided
    coverage: str               # complete | unknown
    scope: dict

    @property
    def available(self) -> bool:
        return self.status == "success"

    def is_stale(self, as_of: datetime) -> bool:
        if not self.available or self.observed_at is None or self.freshness_budget is None:
            return False
        return (as_of - self.observed_at) > self.freshness_budget


# --------------------------------------------------------------------------- #
# identity keys -- deterministic, tenant- and scope-qualified
# --------------------------------------------------------------------------- #
def ec2_key(tenant: str, account: str, region: str, instance_id: str) -> str:
    return f"ec2|{tenant}|{account}|{region}|{instance_id}"


def rds_key(tenant: str, account: str, region: str, db_id: str) -> str:
    return f"rds|{tenant}|{account}|{region}|{db_id}"


def k8s_key(tenant: str, cluster: str, kind: str, namespace: str, name: str) -> str:
    return f"k8s_{kind.lower()}|{tenant}|{cluster}|{namespace}|{name}"


def app_key(tenant: str, service_id: str) -> str:
    return f"app|{tenant}|{service_id}"


def region_from_az(az: str) -> str:
    return re.sub(r"[a-z]$", "", az)


def parse_arn(arn: str) -> dict:
    # arn:aws:<svc>:<region>:<account>:<resource...>
    p = arn.split(":")
    return {"service": p[2], "region": p[3], "account": p[4]}


# --------------------------------------------------------------------------- #
# observation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Observation:
    snapshot_id: str
    tenant_id: str
    family: str
    kind: str                   # ec2_instance | rds_db | tf_resource | k8s_* | catalog_app
    native_id: str
    identity_key: str           # cross-source identity of the underlying object
    observed_at: Optional[datetime]
    attrs: dict
    locator: str                # file + record locator, for evidence references

    def ref(self) -> dict:
        return {"snapshot_id": self.snapshot_id, "locator": self.locator,
                "observed_at": self.observed_at.isoformat() if self.observed_at else None}


@dataclass(frozen=True)
class Link:
    src: str                    # identity_key
    dst: str                    # identity_key
    strength: Strength
    basis: str
    kind: str                   # e.g. "runtime", "management", "dependency"


# --------------------------------------------------------------------------- #
# the resolved world
# --------------------------------------------------------------------------- #
@dataclass
class World:
    as_of: datetime
    tenants: list
    query_defaults: dict
    collections: dict           # snapshot_id -> Collection
    observations: list          # all Observations (sorted, deterministic)
    links: list                 # all Links (sorted, deterministic)

    # -- collection helpers --
    def collection(self, snapshot_id: str) -> Collection:
        return self.collections[snapshot_id]

    def collections_for(self, family=None, tenant=None, environment=None, available=None):
        out = []
        for c in self.collections.values():
            if family and c.family != family:
                continue
            if tenant and c.tenant_id != tenant:
                continue
            if environment is not None:
                env = c.scope.get("environment")
                envs = c.scope.get("environments")
                if env is not None and env != environment:
                    continue
                if envs is not None and environment not in envs:
                    continue
            if available is not None and c.available != available:
                continue
            out.append(c)
        return sorted(out, key=lambda c: c.snapshot_id)

    # -- observation helpers (tenant scope is enforced by the caller's filters) --
    def obs(self, kind=None, tenant=None, key=None):
        out = []
        for o in self.observations:
            if kind and o.kind != kind:
                continue
            if tenant and o.tenant_id != tenant:
                continue
            if key and o.identity_key != key:
                continue
            out.append(o)
        return out

    def observed_stale(self, o: Observation) -> bool:
        return self.collection(o.snapshot_id).is_stale(self.as_of)

    # -- graph --
    def reach(self, start_key, min_strength=Strength.HEURISTIC):
        """Widest-path over links: for every reachable key, the strongest
        achievable bottleneck (weakest-link) strength, and the predecessor edge."""
        bottleneck = {start_key: Strength.EXACT}
        via = {start_key: None}
        changed = True
        while changed:
            changed = False
            for l in self.links:
                if l.strength < min_strength:
                    continue
                for u, v, e in ((l.src, l.dst, l), (l.dst, l.src, l)):
                    if u in bottleneck:
                        cand = min(bottleneck[u], l.strength)
                        if v not in bottleneck or cand > bottleneck[v]:
                            bottleneck[v] = cand
                            via[v] = (e, u)
                            changed = True
        return bottleneck, via

    def path(self, via, target):
        edges, cur = [], target
        while via.get(cur):
            e, pred = via[cur]
            edges.append((pred, cur, e))
            cur = pred
        return list(reversed(edges))
