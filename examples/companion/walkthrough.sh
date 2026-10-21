#!/usr/bin/env bash
# The whole companion flow with nothing but curl. Start the companion first:
#   steadygate serve -c examples/companion/steadygate.yml
# then run:  bash examples/companion/walkthrough.sh
#
# It pushes simulated memory readings so it works anywhere; to sample a real process instead,
# start sessions with: -d '{"collectors":[{"id":"process","params":{"name":"myapp"}}]}'
set -euo pipefail

URL="${STEADYGATE_URL:-http://127.0.0.1:7777}"
JSON=(-H "content-type: application/json")

start() { curl -sf -X POST "$URL/sessions" "${JSON[@]}" -d '{}' | grep -o '"id":"s_[0-9a-f]*"' | cut -d'"' -f4; }
push() { curl -sf -X POST "$URL/sessions/$1/metrics" "${JSON[@]}" -d "$2" > /dev/null; }

echo "== what can it gather?"
curl -sf "$URL/collectors" | grep -o '"id":"[a-z]*"' | cut -d'"' -f4 | sed 's/^/  /'

echo "== record 3 baseline runs"
for mb in 100 101 99; do
  id=$(start)
  push "$id" "[{\"metric\":\"rss_mb\",\"value\":$mb},{\"metric\":\"rss_mb\",\"value\":$mb.5}]"
  curl -sf -X POST "$URL/sessions/$id/stop" > /dev/null
  curl -sf -X POST "$URL/sessions/$id/record"; echo
done

echo "== suggested rule"
curl -sf "$URL/baseline/suggest?metric=rss_mb"; echo

echo "== a run that grew 40%"
id=$(start)
push "$id" '{"metric":"rss_mb","value":140}'
curl -sf -X POST "$URL/sessions/$id/gate" | grep -o '"verdict":"[a-z_]*"'

echo "== a normal run"
id=$(start)
push "$id" '{"metric":"rss_mb","value":100.2}'
stats=$(curl -sf "$URL/sessions/$id/stats?metric=rss_mb")
echo "  stats: $stats"
verdict=$(curl -sf -X POST "$URL/sessions/$id/gate" | grep -o '"verdict":"[a-z_]*"' | cut -d'"' -f4)
echo "verdict: $verdict"
