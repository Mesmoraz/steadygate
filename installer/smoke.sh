#!/usr/bin/env bash
# Smoke-test a built steadygate binary against examples/basic.
# Usage: installer/smoke.sh <path-to-steadygate-binary>
set -u

[ $# -eq 1 ] || { echo "usage: $0 <cli-binary>"; exit 2; }
BIN="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
[ -f "$BIN" ] || { echo "no such binary: $1"; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cp -R "$ROOT/examples/basic/." "$WORK/"
rm -rf "$WORK/.steadygate"
cd "$WORK" || exit 1

for i in 1 2 3; do
  "$BIN" record "baseline-$i.jsonl" --run-id "base-$i" || { echo "record failed"; exit 1; }
done

"$BIN" run candidate-noise.jsonl ; test $? -eq 0 || { echo "expected 0"; exit 1; }
"$BIN" run candidate-regression.jsonl ; test $? -eq 1 || { echo "expected 1"; exit 1; }
"$BIN" run candidate-broken.jsonl ; test $? -eq 2 || { echo "expected 2"; exit 1; }

echo "smoke: ok"
