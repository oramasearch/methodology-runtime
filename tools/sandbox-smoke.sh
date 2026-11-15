#!/bin/sh
# SPDX-License-Identifier: MIT

# Smoke-test a locally built runtime image the way the run pod uses it, with no registry involved:
# build the example methodology's image FROM it, stage a bundle, run it with no network, no
# environment, a read-only root and no capabilities, and show the sandbox refusing a credential and
# network it was not granted.
#
#   tools/sandbox-smoke.sh [runtime image] [container command]
#   default image: methodology-runtime:local (docker build -t methodology-runtime:local .)
set -eu
image=${1:-methodology-runtime:local}
engine=${2:-docker}
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
methodology_image="methodology-smoke:local"

echo "== build the example methodology FROM $image"
"$engine" build -q -f "$root/templates/methodology.Dockerfile" \
    --build-arg METHODOLOGY_RUNTIME_IMAGE="$image" -t "$methodology_image" "$root/examples/logistic" >/dev/null

echo "== stage a bundle (what the stage pod writes)"
mkdir -p "$work/in" "$work/out"
python3 - "$root" "$work/in" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
from tests.support import GOOD, manifest, rows
spec = {"interface": 1, "run_id": "00000000-0000-4000-8000-000000000001", "mode": "train",
        "methodology_id": "W-0123abcd", "params": {}, "target": "churned",
        "features": ["tenure", "tickets"], "holdout_fraction": 0.2, "seed": 42, "network": False,
        "manifest": manifest(GOOD)}
open(sys.argv[2] + "/spec.json", "w").write(json.dumps(spec))
with open(sys.argv[2] + "/data.jsonl", "w") as handle:
    for row in rows(300):
        handle.write(json.dumps(row) + "\n")
PY
chmod -R a+rwX "$work"

sandbox="--rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 --tmpfs /tmp"
mounts="-v $work/in:/work/in:ro -v $work/out:/work/out"

echo "== run with no network and no credential: expect status ok"
# shellcheck disable=SC2086
"$engine" run $sandbox $mounts "$methodology_image" run
python3 -c "import json,sys; r=json.load(open('$work/out/result.json')); print(r['status'], [f['check'] for f in r['findings'] if f['check'].startswith('sandbox')], r['metrics'].get('held_out_auc'))"

echo "== run with a credential in the environment: expect exit 3, the code not run"
set +e
# shellcheck disable=SC2086
"$engine" run $sandbox $mounts -e LAKE_TOKEN=not-a-real-credential "$methodology_image" run
echo "exit $?"
echo "== run with network it was not granted: expect exit 3 where the host has network"
# shellcheck disable=SC2086
"$engine" run --rm --read-only --cap-drop ALL --user 10001:10001 --tmpfs /tmp $mounts "$methodology_image" run
echo "exit $?"
set -e
"$engine" rmi -f "$methodology_image" >/dev/null
