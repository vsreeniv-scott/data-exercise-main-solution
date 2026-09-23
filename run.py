"""Deterministic rebuild + the two required answers + the issues report.

Usage: python3 run.py [data_root]   (default: ./data)
Writes answers/answers.json and answers/issues.json; prints a summary.
"""
import json
import os
import sys

from contextlayer.build import build_world
from contextlayer.questions import question_a, question_b
from contextlayer.issues import issues_report
from contextlayer.model import human_age


def main(root="data"):
    world = build_world(root)
    q = world.query_defaults
    tenant = q.get("tenant_id", "acme")
    env = q.get("environment", "prod")
    app = q.get("application_name", "payments-api")

    a = question_a(world, tenant, env)
    b = question_b(world, tenant, env, app)
    issues = issues_report(world)

    os.makedirs("answers", exist_ok=True)
    with open("answers/answers.json", "w") as f:
        json.dump({"question_a": a, "question_b": b}, f, indent=2, sort_keys=False)
    with open("answers/issues.json", "w") as f:
        json.dump(issues, f, indent=2, sort_keys=False)

    print(f"Rebuild @ as_of={world.as_of.isoformat()}   tenant={tenant} env={env} app={app}")
    print("\nCollection states:")
    for c in sorted(world.collections.values(), key=lambda x: x.snapshot_id):
        st = "available" if c.available else "UNAVAILABLE"
        extra = ""
        if c.available:
            extra = "  STALE" if c.is_stale(world.as_of) else "  fresh"
            extra += f" ({human_age(c.observed_at, world.as_of)})"
        print(f"  {c.snapshot_id:40} {c.family:11} {st}{extra}")

    print("\n== Question A ==")
    print(f"  summary: {a['summary_counts']}")
    for r in a["instances"]:
        print(f"  {r['instance_id']}  {r['binding_status']}")

    print("\n== Question B ==")
    print(f"  app team owner: {b['application']['application_team_owner']}")
    print(f"  dependency present: {b['declared_dependency']['present_in_inventory']}")
    print("  runtime path to cloud:")
    for h in b["runtime"]["path_to_cloud"]:
        print(f"    -> {h['instance']}  [{h['overall_strength'].split(' (')[0]}]")
    for e in b["infrastructure_operator_evidence"]:
        tag = "CONFLICT" if e["conflict"] else "agree"
        print(f"  operator {e['instance']}: {tag} -> {[s['operator_team'] for s in e['statements']]}")

    print(f"\n== Issues == ({issues['count']})")
    for i in issues["issues"]:
        print(f"  [{i['severity']}] {i['type']}")

    print("\nWrote answers/answers.json and answers/issues.json")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data")
