#!/bin/bash
# Retimer FW upgrade/downgrade endurance loop (100x by default), with an
# AC-cycle checkpoint every 10 cycles. See retimer_fw_cycle_stress.py --help
# for all options, e.g.:
#   ./run_retimer_stress.sh --bmc-ip 10.0.0.5
#   ./run_retimer_stress.sh --bmc-ip 10.0.0.5 --cards retimer1 --cycles 20
uv run --no-sync -- python retimer_fw_cycle_stress.py "$@"
rc="$?"
exit "${rc}"
