#!/usr/bin/env bash
# Linux/root. Two private network namespaces; no traffic uses the host LAN.
set -euo pipefail
script=$(cd "$(dirname "$0")" && pwd)/maas-distribution-egress
work=$(mktemp -d -t tmp_agent_egress.XXXXXXXX)
send="tmp_agent_egress_s_${work##*.}"
recv="tmp_agent_egress_r_${work##*.}"
namespaces=()
servers=()
cleanup() {
    for pid in "${servers[@]}"; do kill "$pid" 2>/dev/null || true; done
    for pid in "${servers[@]}"; do wait "$pid" 2>/dev/null || true; done
    for ns in "${namespaces[@]}"; do ip netns del "$ns"; done
    rm -r -- "$work"
}
trap cleanup EXIT
for ns in "$send" "$recv"; do
    ip netns add "$ns"
    namespaces+=("$ns")
    ip -n "$ns" link set lo up
done
ip -n "$send" link add tx0 type veth peer name rx0 netns "$recv"
ip -n "$send" addr add 198.18.0.1/30 dev tx0
ip -n "$recv" addr add 198.18.0.2/30 dev rx0
ip -n "$send" link set tx0 up
ip -n "$recv" link set rx0 up
sender=(ip netns exec "$send")
client=(ip netns exec "$recv" curl --noproxy '*' -fsS --max-time 20 -o /dev/null)
export LINK_MBIT=2500 DISTRIBUTION_MBIT=1500 REGISTRY_PORT=18443
"${sender[@]}" "$script" apply tx0
truncate -s 1M "$work/small"
truncate -s 256M "$work/large"
for port in 2049 "$REGISTRY_PORT" 18831; do
    "${sender[@]}" python3 -B -m http.server "$port" --bind 198.18.0.1 --directory "$work" >"$work/log-$port" 2>&1 &
    servers+=("$!")
done
for port in 2049 "$REGISTRY_PORT" 18831; do
    "${client[@]}" --retry 5 --retry-connrefused --retry-delay 1 --head "http://198.18.0.1:$port/small" 2>"$work/ready-$port"
done
bytes() {
    "${sender[@]}" tc -j -s qdisc show dev tx0 |
        python3 -Bc 'import json,sys; print(next(row["bytes"] for row in json.load(sys.stdin) if row.get("parent")==sys.argv[1]))' "d150:$1"
}
for port in 2049 "$REGISTRY_PORT" 18831; do
    shared=$(bytes 10)
    ordinary=$(bytes 20)
    "${client[@]}" "http://198.18.0.1:$port/small"
    shared_delta=$(( $(bytes 10) - shared ))
    ordinary_delta=$(( $(bytes 20) - ordinary ))
    if [[ $port == 18831 ]]; then
        (( ordinary_delta >= 1048576 && shared_delta < 65536 ))
    else
        (( shared_delta >= 1048576 && ordinary_delta < 65536 ))
    fi
done
start=$(date +%s%N)
"${client[@]}" -w '%{size_download}' "http://198.18.0.1:2049/large" >"$work/nfs" &
first=$!
"${client[@]}" -w '%{size_download}' "http://198.18.0.1:$REGISTRY_PORT/large" >"$work/registry" &
second=$!
wait "$first"
wait "$second"
end=$(date +%s%N)
python3 -B - "$start" "$end" "$work/nfs" "$work/registry" <<'PY'
from pathlib import Path
import sys
counts = [int(Path(p).read_text()) for p in sys.argv[3:]]
assert counts == [256 * 1024**2] * 2, counts
mbps = sum(counts) * 8e3 / (int(sys.argv[2]) - int(sys.argv[1]))
assert 1200 <= mbps <= 1520, mbps
print(f"Combined TCP payload: {mbps:.1f} Mbit/s. Both distribution ports and default traffic classified.")
PY
"${sender[@]}" "$script" remove tx0
# A pre-existing user qdisc must survive both apply and remove.
"${sender[@]}" tc qdisc add dev tx0 root handle 123: fq_codel
if "${sender[@]}" "$script" apply tx0 2>/dev/null; then exit 1; fi
"${sender[@]}" "$script" remove tx0
"${sender[@]}" tc qdisc show dev tx0 | grep -q '^qdisc fq_codel 123: root'
