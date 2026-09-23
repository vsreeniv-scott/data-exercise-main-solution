"""build.py -- deterministic full rebuild from the supplied files."""

from __future__ import annotations

import json
import os

from .model import World, parse_ts
from .ingest import load_collections, ingest
from .resolve import build_links


def build_world(root: str) -> World:
    with open(os.path.join(root, "manifest.json")) as f:
        manifest = json.load(f)

    collections = load_collections(manifest)
    observations = ingest(root, collections)
    observations.sort(key=lambda o: (o.identity_key, o.snapshot_id, o.kind, o.locator))

    # print(collections)
    # print(observations)

    links = build_links(observations)

    return World(
       as_of=parse_ts(manifest["as_of"]),
        tenants=list(manifest.get("tenants", [])),
        query_defaults=manifest.get("query_defaults", {}),
        collections=collections,
        observations=observations,
        links=links)
