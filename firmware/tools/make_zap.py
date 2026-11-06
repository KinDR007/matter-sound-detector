#!/usr/bin/env python3
"""Generate src/default_zap/sound_detector.zap.

Endpoint 0: root node (from the NCS Matter template).
Endpoints 1..N: one Generic Switch (momentary, InitialPress event) per sound
class, plus Fixed Label so Home Assistant names each event entity
(label "ha_entitylabel", honoured for test vendor 0xFFF1 / PID 0x8000).

Usage: tools/make_zap.py [NCS_DIR]   (default /opt/nordic/ncs/v3.4.2)
Then:  scripts/ncs.sh west zap-generate -z <zap> --full
"""
import copy
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
NCS = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/nordic/ncs/v3.4.2")
MATTER = NCS / "modules" / "lib" / "matter"
TEMPLATE = NCS / "nrf" / "samples" / "matter" / "template" / "src" / "default_zap" / "template.zap"
CHEF = MATTER / "examples" / "chef" / "devices" / "rootnode_genericswitch_2dfff6e516.zap"
ALL = MATTER / "examples" / "all-clusters-app" / "all-clusters-common" / "all-clusters-app.zap"
MODEL_H = HERE / "src" / "model_data.h"
OUT = HERE / "src" / "default_zap" / "sound_detector.zap"

SWITCH_FEATURE_MS = 0x2  # Momentary Switch


def find_cluster(zap, name, side="server"):
    for et in zap["endpointTypes"]:
        for c in et["clusters"]:
            if c["name"] == name and c["side"] == side and c["enabled"]:
                return copy.deepcopy(c)
    raise SystemExit(f"cluster {name} not found")


def set_attr(cluster, name, value):
    for a in cluster["attributes"]:
        if a["name"] == name:
            a["defaultValue"] = value
            return
    raise SystemExit(f"attribute {name} not in {cluster['name']}")


def main():
    for p in (TEMPLATE, CHEF, ALL, MODEL_H):
        if not p.exists():
            sys.exit(f"missing {p}")
    n_classes = int(re.search(r"#define MD_N_CLASSES (\d+)", MODEL_H.read_text()).group(1))

    zap = json.loads(TEMPLATE.read_text())
    chef = json.loads(CHEF.read_text())
    allc = json.loads(ALL.read_text())

    for pkg in zap["package"]:
        name = Path(pkg["path"]).name
        target = MATTER / "src" / "app" / "zap-templates" / ("zcl" if name == "zcl.json" else "") / name
        pkg["path"] = os.path.relpath(target, OUT.parent)

    sw_et = copy.deepcopy(chef["endpointTypes"][1])
    sw_et["id"] = len(zap["endpointTypes"]) + 1
    sw_et["name"] = "MA-genericswitch"
    keep = {"Identify", "Descriptor", "Switch"}
    sw_et["clusters"] = [c for c in sw_et["clusters"] if c["name"] in keep]
    dt = [d for d in sw_et["deviceTypes"] if d["name"] == "MA-genericswitch"]
    sw_et["deviceTypes"] = dt
    sw_et["deviceVersions"] = [sw_et["deviceVersions"][0]]
    sw_et["deviceIdentifiers"] = [15]

    for c in sw_et["clusters"]:
        if c["name"] == "Switch":
            set_attr(c, "NumberOfPositions", "2")
            set_attr(c, "FeatureMap", hex(SWITCH_FEATURE_MS))
            for e in c.get("events", []):
                e["included"] = 1 if e["name"] == "InitialPress" else 0
    sw_et["clusters"].append(find_cluster(allc, "Fixed Label"))

    zap["endpointTypes"].append(sw_et)
    idx = len(zap["endpointTypes"]) - 1
    for i in range(n_classes):
        zap["endpoints"].append({
            "endpointTypeName": sw_et["name"],
            "endpointTypeIndex": idx,
            "profileId": 259,
            "endpointId": i + 1,
            "networkId": 0,
            "parentEndpointIdentifier": None,
        })

    OUT.write_text(json.dumps(zap, indent=2) + "\n")
    print(f"wrote {OUT} with {n_classes} Generic Switch endpoints")


if __name__ == "__main__":
    main()
