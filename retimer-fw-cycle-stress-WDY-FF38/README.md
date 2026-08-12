# Retimer FW Cycle Stress Test (retimer-fw-cycle-stress-WDY-FF38)

Client-server endurance test for the K2V5 / Cordite retimer boards on
Frankfurter38cx2 (WDY-FF38). It loops retimer FW **upgrade → downgrade**
100 times (configurable) and periodically power-cycles the DUT to confirm
the flashed firmware version survives an AC cycle.

- **Server** = the machine running `retimer_fw_cycle_stress.py` (this repo).
  It decides what to flash next, pushes the FW image and
  `scripts/bmc/flash_retimer_fw.sh` to the BMC over SCP, triggers the flash
  over SSH, and records the pass/fail result reported back.
- **Client** = the DUT's Nitro BMC. All I2C access (EEPROM write, GPIO
  reset, `retimer_app --get-fw-version` read-back) happens on the BMC via
  `flash_retimer_fw.sh`, which is the same script also used by the BFT
  (Board Functional Test) `flash_retimer_fw` step — just looped here.

## Contents

```
retimer-fw-cycle-stress-WDY-FF38/
├── retimer_fw_cycle_stress.py   # main test script (the "server")
├── run_retimer_stress.sh        # thin wrapper: uv run --no-sync -- python retimer_fw_cycle_stress.py "$@"
├── pyproject.toml               # dependencies (loguru, pexpect)
├── bin_file/                    # put your two retimer FW images here (see bin_file/README.md)
├── libs/
│   ├── client.py                 # NitroBMC class: ssh/scp/power/reboot/get-fw-version wrappers
│   ├── helpers.py                # execute/ssh_run/scp_to/ping/retry helpers
│   ├── frugen.py                  # FRU helpers (not used by this test, kept for completeness)
│   └── fru_parser.py              # FRU binary parser (not used by this test, kept for completeness)
└── scripts/bmc/
    └── flash_retimer_fw.sh       # runs ON the BMC: I2C flash + retimer_app version check
```

## Prerequisites

1. **Automation host** (Linux recommended; needs `ssh`/`scp`/`ping` on PATH):
   - Python 3.10+
   - [`uv`](https://github.com/astral-sh/uv) (optional, for `run_retimer_stress.sh`) — or just
     `pip install loguru pexpect` and run the `.py` file directly.
   - An SSH key at `~/.ssh/id_ecdsa` that is authorized on the DUT's BMC
     (`root@<bmc_ip>`). `libs/helpers.py` always uses this key path for
     `ssh`/`scp`. If the BMC doesn't have your key yet, either provision it
     manually first, or let the script call `NitroBMC.setup_ssh_and_scp_scripts()`,
     which installs SSH keys via `libs/helpers.setup_bmc_ssh()` (requires the
     `coap` CLI and `keg-install` assets from the original BFT framework —
     not bundled here; provision the BMC's SSH access ahead of time if you
     don't have those).
   - `nitro-bmc` CLI on PATH (used for `power cycle` / `power status` / `bmc info`).
2. **DUT**: Frankfurter38cx2 / Hotdog38 system with K2V5 Cordite retimer
   board(s) installed and BMC reachable over the network.
3. **Firmware images**: two known-good retimer FW binaries — one to use as
   the "upgrade" version and one as the "downgrade" version — placed under
   `bin_file/` (see [bin_file/README.md](bin_file/README.md)). FW binaries
   are intentionally **not** committed to this repo (`.gitignore` excludes
   `bin_file/*.bin`); copy your own vendor-provided images in.

## Setup

```bash
cd retimer-fw-cycle-stress-WDY-FF38

# Option A: uv (recommended)
uv sync

# Option B: plain pip
pip install loguru pexpect

# Copy your two FW images into bin_file/, e.g.:
cp /path/to/pt516_x16_normal_hyve__SRNS__2.13_Ryan__v2_13_9.bin bin_file/
cp /path/to/pt516_x16_normal_SRNS_v2_13_0.bin bin_file/
```

## Usage

```bash
# Using the wrapper (uv):
./run_retimer_stress.sh --bmc-ip 10.0.0.5

# Or directly with python:
python retimer_fw_cycle_stress.py --bmc-ip 10.0.0.5
```

If `--bmc-ip` is omitted, the script prompts for it interactively.

### Common examples

```bash
# Full default run: 100 cycles, AC-cycle checkpoint every 10 cycles, both cards
./run_retimer_stress.sh --bmc-ip 10.0.0.5

# Quick smoke test: 5 cycles, checkpoint every cycle
./run_retimer_stress.sh --bmc-ip 10.0.0.5 --cycles 5 --checkpoint-interval 1

# Only test Cordite 0 (retimer1)
./run_retimer_stress.sh --bmc-ip 10.0.0.5 --cards retimer1

# Custom FW images / versions
./run_retimer_stress.sh --bmc-ip 10.0.0.5 \
    --upgrade-bin bin_file/my_new_fw.bin --upgrade-version 3.0.0 \
    --downgrade-bin bin_file/my_old_fw.bin --downgrade-version 2.13.0

# Keep looping even after a failed step (for unattended overnight soak, gather all data)
./run_retimer_stress.sh --bmc-ip 10.0.0.5 --continue-on-failure
```

### CLI options

| Option | Default | Description |
|---|---|---|
| `--bmc-ip` | *(prompted)* | Nitro BMC IP of the DUT. |
| `--cards` | `retimer1,retimer2` | Comma-separated cards to test: `retimer1` (Cordite 0, I2C bus 53), `retimer2` (Cordite 1, I2C bus 45). |
| `--cycles` | `100` | Total upgrade/downgrade cycles to run. |
| `--checkpoint-interval` | `10` | Run an AC-cycle checkpoint every N cycles. |
| `--upgrade-bin` | `bin_file/pt516_x16_normal_hyve__SRNS__2.13_Ryan__v2_13_9.bin` | FW image used for the "upgrade" step. |
| `--upgrade-version` | `2.13.9` | Target version string for the upgrade image. |
| `--downgrade-bin` | `bin_file/pt516_x16_normal_SRNS_v2_13_0.bin` | FW image used for the "downgrade" step. |
| `--downgrade-version` | `2.13.0` | Target version string for the downgrade image. |
| `--log-dir` | `logs/retimer_stress/<timestamp>` | Directory for CSV/JSON results. |
| `--post-ac-settle-s` | `90` | Seconds to wait after host power is back on before reading FW version. |
| `--power-status-timeout` | `180` | Seconds to wait for host `power status` to report `on`. |
| `--bmc-back-timeout` | `180` | Seconds to wait for BMC ping/SSH/`bmc info` to come back after a power cycle. |
| `--continue-on-failure` | *(off)* | Keep looping after a failed step instead of aborting immediately. |

Run `python retimer_fw_cycle_stress.py --help` for the full, always-up-to-date list.

## Test flow

**Initial stage**
1. Ping/SSH-check the BMC and confirm each selected retimer is accessible
   (`retimer_app --get-fw-version` succeeds).
2. Flash the **downgrade** FW on every selected card and verify the version,
   establishing a known-good baseline before the loop starts.

**Testing stage** (repeats until `--cycles` is reached)

3. Flash the **upgrade** FW, check version.
4. Flash the **downgrade** FW, check version.
   (3⇒4 is one "cycle" — the same flash+verify logic as the BFT
   `flash_retimer_fw` test, just looped.)
5. Every `--checkpoint-interval` cycles: AC-cycle the DUT
   (`nitro-bmc -i $BMC_IP power cycle`), wait for the BMC/host to come back,
   then re-read the retimer FW version to confirm it loaded correctly across
   the power cycle.
6. Repeat 3⇒4 (with checkpoints) until the total cycle count is reached.

Both K2V5 cards (**Cordite 0** = `retimer1` / I2C bus 53, **Cordite 1** =
`retimer2` / I2C bus 45) are exercised every cycle by default, since a single
AC cycle power-cycles the whole DUT — and therefore both cards — at once.
Use `--cards retimer1` or `--cards retimer2` to test only one card.

## Output

Results are written under `--log-dir` (default `logs/retimer_stress/<timestamp>/`):

- **`retimer_stress_results.csv`** — one row per step (`init_downgrade`,
  `upgrade`, `downgrade`, `ac_cycle_check`), streamed live so partial results
  are preserved even if the run is interrupted. Columns:
  `cycle, card, step, expected_version, actual_version, status, duration_s, timestamp, detail`.
- **`retimer_stress_summary.json`** — written at the end (or on
  abort/Ctrl+C): per-card pass/fail counts and the cycle/step of the first
  failure, plus an overall `overall_pass` boolean.

The script exits `0` on full success, non-zero otherwise (also printed to
stdout as JSON).

## Notes / limitations

- This bundle only includes what's needed for the retimer FW flash flow;
  other BFT check scripts (bridge, DVD/JRD, voltage/I2C presence checks,
  etc.) from the original framework were intentionally left out per scope.
- `libs/helpers.py` uses hardcoded default credentials (BMC `admin/admin`,
  host OS `root/123456`) consistent with the vendor's manufacturing/test
  defaults — update these if your lab uses different credentials.
- Tune `--post-ac-settle-s` / `--power-status-timeout` / `--bmc-back-timeout`
  to match your platform's actual boot/settle timing before an unattended
  100-cycle run.
