#!/bin/sh
# Run as root on a disposable Linux VM with systemd, dmsetup and a free loop device:
# sh tests/bootstrap/verify_boot_recovery_systemd.sh
# Exercise the shipped service policy with an isolated unit and a stub worker.
set -eu
repo=$(cd "$(dirname "$0")/../.." && pwd)
source_unit=$repo/deploy/bootstrap/clone/maas-worker-bootstrap.service
scratch=$(mktemp -d /run/tmp_agent_boot_recovery.XXXXXXXX)
unit=${scratch##*/}.service
config=/run/systemd/system/$unit
device=${scratch##*/}
loop=
mapped=false
cleanup() {
    if "$mapped"; then dmsetup resume "$device" 2>/dev/null || true; fi
    systemctl stop "$unit" >/dev/null 2>&1 || true
    systemctl reset-failed "$unit" >/dev/null 2>&1 || true
    if "$mapped"; then dmsetup remove --retry "$device"; fi
    if [ -n "$loop" ]; then losetup -d "$loop"; fi
    rm -f "$config" "$scratch/disk" "$config.d/test.conf" "$scratch/attempt" "$scratch/count" "$scratch/mode" "$scratch/apply.lock"
    if [ -d "$config.d" ]; then rmdir "$config.d"; fi
    rmdir "$scratch"
    systemctl daemon-reload
}
trap cleanup EXIT
trap 'exit 1' INT TERM
truncate -s 1M "$scratch/disk"
loop=$(losetup --read-only --find --show "$scratch/disk")
dmsetup create "$device" --readonly --table "0 2048 linear $loop 0"
mapped=true
udevadm settle --timeout=10
cp "$source_unit" "$config"
mkdir "$config.d"
cat > "$config.d/test.conf" <<EOF
[Service]
ExecStart=
ExecStart=/bin/sh $scratch/attempt $scratch
TimeoutStartSec=3
RestartSec=1
EOF
cat > "$scratch/attempt" <<'EOF'
set -eu
dir=$1
echo attempt >> "$dir/count"
case "$(cat "$dir/mode")" in
    denied) exit 1 ;;
    transient) test "$(wc -l < "$dir/count")" -gt 1 || exit 75 ;;
    io-timeout) exec dd if="/dev/mapper/${dir##*/}" of=/dev/null bs=4096 count=1 iflag=direct status=none ;;
esac
EOF
systemctl daemon-reload
for mode in transient io-timeout denied; do
    : > "$scratch/count"
    echo "$mode" > "$scratch/mode"
    if [ "$mode" = io-timeout ]; then dmsetup suspend "$device"; fi
    systemctl start --no-block "$unit"
    if [ "$mode" = io-timeout ]; then
        for _ in $(seq 1 30); do
            pid=$(systemctl show -P MainPID "$unit")
            if [ -r "/proc/$pid/stat" ] && awk '$2=="(dd)" && $3=="D" {found=1} END {exit !found}' "/proc/$pid/stat"; then
                break
            fi
            sleep 0.05
        done
        awk '$2=="(dd)" && $3=="D" {found=1} END {exit !found}' "/proc/$pid/stat"
        for _ in $(seq 1 50); do
            [ "$(systemctl show -P Result "$unit")" = timeout ] && break
            sleep 0.1
        done
        test "$(systemctl show -P Result "$unit")" = timeout
        dmsetup resume "$device"
    fi
    expected=active
    count=2
    if [ "$mode" = denied ]; then expected=failed; count=1; fi
    for _ in $(seq 1 60); do
        [ "$(systemctl show -P ActiveState "$unit")" = "$expected" ] && break
        sleep 0.25
    done
    test "$(systemctl show -P ActiveState "$unit")" = "$expected"
    sleep 2
    test "$(wc -l < "$scratch/count")" -eq "$count"
    echo "$mode: $count attempt(s), $expected"
    systemctl stop "$unit" || true
    systemctl reset-failed "$unit" 2>/dev/null || true
done

# Exercise the actual Python deadline and inherited lock while the read is in D state.
dmsetup suspend "$device"
PYTHONPATH="$repo" PYTHONDONTWRITEBYTECODE=1 timeout -k 2s 15s python3 - "$device" "$scratch" <<'PY'
import subprocess
import sys
import time
from maas_bootstrap.client import BootstrapError, LocalState
from maas_bootstrap.startup import command

device, scratch = sys.argv[1:]
read = ['dd', f'if=/dev/mapper/{device}', 'of=/dev/null', 'bs=4096', 'count=1',
        'iflag=direct', 'status=none']
state = LocalState(scratch)
try:
    with state.lock() as lock_fd:
        command(read, 0.5, lock_fd=lock_fd)
except BootstrapError as error:
    assert error.code == 'BOOT_COMMAND_STILL_RUNNING' and error.retry_boot
else:
    raise AssertionError('suspended device read completed')
try:
    with state.lock():
        raise AssertionError('second attempt entered while the first read was stuck')
except BootstrapError as error:
    assert error.code == 'BOOTSTRAP_BUSY'
subprocess.run(['dmsetup', 'resume', device], check=True)
deadline = time.monotonic() + 5
while True:
    try:
        with state.lock() as lock_fd:
            assert command(read, 2, lock_fd=lock_fd)[0] == 0
        break
    except BootstrapError as error:
        if error.code != 'BOOTSTRAP_BUSY' or time.monotonic() >= deadline:
            raise
        time.sleep(0.1)
print('blocked read: bounded cleanup, lock held, successful read after resume')
PY
