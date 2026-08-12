#!/bin/sh
set -eu

LOCK_FILE="/tmp/flash_retimer_fw.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Error: another instance of flash_retimer_fw.sh is already running" >&2
    exit 1
fi

usage() {
    echo "Usage: $0 <image_file> <retimer1|retimer2> <target_version>"
    echo "Example: $0 /tmp/retimer.bin retimer2 2.13.0"
}

if [ "$#" -ne 3 ]; then
    usage
    exit 1
fi

image_file="$1"
retimer_id="$2"
target_version="$3"
retimer_app="/var/env/halon-bmc/loaded/bin/retimer_app"

case "$retimer_id" in
    retimer1)
        bus="53"
        ;;
    retimer2)
        bus="45"
        ;;
    *)
        echo "Error: invalid retimer '$retimer_id' (must be retimer1 or retimer2)" >&2
        usage
        exit 1
        ;;
esac

run() {
    echo "+ $*"
    "$@"
}

run_capture() {
    echo "+ $*" >&2
    "$@"
}

if [ ! -f "$image_file" ]; then
    echo "Error: image file not found: $image_file" >&2
    exit 1
fi

run i2cset -f -y "$bus" 0x20 0x03 0xf5
run i2cset -f -y "$bus" 0x20 0x01 0xf5
run i2cget -f -y "$bus" 0x50 0x00
run dd if="$image_file" of="/sys/bus/i2c/devices/${bus}-0050/eeprom"

gpio_name="$(run_capture gpiodetect | awk -v b="${bus}-0020" '$0 ~ b {print $1; exit}')"
if [ -z "$gpio_name" ]; then
    echo "Error: cannot find gpio chip for ${bus}-0020" >&2
    exit 1
fi
echo "GPIO chip: $gpio_name"

run gpioset "$gpio_name" 2=0
run sleep 0.5
run gpioset "$gpio_name" 2=1
run sleep 5

# --get-fw-version: up to 3 tries (initial + 2 retries, 5s apart) if retimer_app exits non-zero
max_fw_attempts=3
attempt=1
fw_json=""
while [ "$attempt" -le "$max_fw_attempts" ]; do
    echo "+ $retimer_app --bus $bus --device 35 --vendor astera --get-fw-version" >&2
    rc=0
    fw_json="$("$retimer_app" --bus "$bus" --device 35 --vendor astera --get-fw-version)" || rc=$?
    if [ "$rc" -eq 0 ]; then
        if [ "$attempt" -gt 1 ]; then
            echo "Note: --get-fw-version succeeded on attempt $attempt of $max_fw_attempts." >&2
        fi
        break
    fi
    echo "Warning: --get-fw-version failed with exit code $rc (attempt $attempt of $max_fw_attempts)." >&2
    if [ "$attempt" -eq "$max_fw_attempts" ]; then
        echo "Error: --get-fw-version failed after $max_fw_attempts attempts." >&2
        exit 1
    fi
    echo "Retrying in 5 seconds..." >&2
    sleep 5
    attempt=$((attempt + 1))
done
echo "FW info: $fw_json"

current_version="$(printf '%s\n' "$fw_json" | sed -n 's/.*"Version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
if [ -z "$current_version" ]; then
    echo "Error: unable to parse Version from output: $fw_json" >&2
    exit 1
fi

echo "Current version: $current_version"
echo "Target version:  $target_version"

if [ "$current_version" != "$target_version" ]; then
    echo "Error: version mismatch (expected $target_version, got $current_version)" >&2
    exit 1
fi

echo "Flash and version check completed successfully."
