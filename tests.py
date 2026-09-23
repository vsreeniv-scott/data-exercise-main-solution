"""
Focused tests for the decisions that matter. Run: python3 tests.py  (or pytest).
Each test names the implementation error it would catch.
"""
import json
from contextlayer.build import build_world
from contextlayer.questions import question_a, question_b
from contextlayer.resolve import resolve_dependency_target

W = build_world("data")


def test_justified_match_and_path_excludes_name_lookalike():
    """Catches: matching an app to an instance by its Name tag instead of the
    workload->node->providerID chain."""
    b = question_b(W, "acme", "prod", "payments-api")
    reached = {h["identity_key"].split("|")[-1] for h in b["runtime"]["path_to_cloud"]}
    assert reached == {"i-00000000000000101", "i-00000000000000102"}
    # the EC2 literally named "payments-api" is i-...103 and must NOT be on the path
    assert "i-00000000000000103" not in reached


def test_tenant_isolation_on_shared_account():
    """Catches: fusing two tenants because they share an AWS account / instance id."""
    b = question_b(W, "acme", "prod", "payments-api")
    assert b["application"]["application_team_owner"] == "team-payments"   # not team-bravo-apps
    a = question_a(W, "acme", "prod")
    ids = {r["instance_id"] for r in a["instances"]}
    assert "i-00000000000000101" in ids           # acme's 101
    # acme entity key is tenant-qualified and distinct from bravo's identical id
    keys = {r["identity_key"] for r in a["instances"]}
    assert "ec2|acme|111111111111|us-east-1|i-00000000000000101" in keys
    assert "ec2|bravo|111111111111|us-east-1|i-00000000000000101" not in keys


def test_terraform_data_is_not_management():
    """Catches: treating a Terraform data source as a managed binding."""
    a = question_a(W, "acme", "prod")
    by_id = {r["instance_id"]: r for r in a["instances"]}
    assert by_id["i-00000000000000101"]["binding_status"] == "managed_binding_found"
    assert by_id["i-00000000000000103"]["binding_status"] == "no_managed_binding__data_reference_present"
    assert by_id["i-00000000000000104"]["binding_status"] == "no_binding_found_in_supplied_scope"
    # stopped instance i-...106 must not appear (question asks for running)
    assert "i-00000000000000106" not in by_id


def test_stale_and_unavailable_are_qualified_not_flattened():
    """Catches: using the wall clock (missing staleness) or reading an
    unavailable collection as 'no binding'."""
    # freshness is judged against manifest as_of
    assert W.collection("tf-acme-prod-20260902T090000Z").is_stale(W.as_of) is True
    assert W.collection("aws-acme-prod-20260916T115000Z").is_stale(W.as_of) is False
    # offset-aware timestamp (-04:00) parsed correctly -> fresh
    assert W.collection("k8s-acme-staging-20260916T115300Z").is_stale(W.as_of) is False
    a = question_a(W, "acme", "prod")
    assert all(e["stale"] for r in a["instances"] for e in r["terraform_evidence"] if e["mode"] == "managed")
    # bravo prod: instance present but Terraform not provided -> UNAVAILABLE, not "no binding"
    ab = question_a(W, "bravo", "prod")
    assert any(r["binding_status"] == "management_evidence_unavailable" for r in ab["instances"])


def test_rds_identifier_is_scoped_by_account():
    """Catches: treating 'payments-db' as one database across accounts."""
    keys = {o.identity_key for o in W.observations}
    prod_app = next(o for o in W.obs(kind="catalog_app", tenant="acme")
                    if o.attrs["service_name"] == "payments-api" and o.attrs["environment"] == "prod")
    stg_app = next(o for o in W.obs(kind="catalog_app", tenant="acme")
                   if o.attrs["service_name"] == "payments-api" and o.attrs["environment"] == "staging")
    kp, pp = resolve_dependency_target(prod_app, keys)
    ks, ps = resolve_dependency_target(stg_app, keys)
    assert kp == "rds|acme|111111111111|us-east-1|payments-db" and pp
    assert ks == "rds|acme|222222222222|us-east-1|payments-db" and ps
    assert kp != ks


def test_deterministic_rebuild():
    """Catches: nondeterministic identities/ordering across rebuilds."""
    w2 = build_world("data")
    assert [o.identity_key for o in W.observations] == [o.identity_key for o in w2.observations]
    assert [(l.src, l.dst, l.kind) for l in W.links] == [(l.src, l.dst, l.kind) for l in w2.links]
    a1 = json.dumps(question_a(W, "acme", "prod"), sort_keys=True)
    a2 = json.dumps(question_a(w2, "acme", "prod"), sort_keys=True)
    assert a1 == a2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
