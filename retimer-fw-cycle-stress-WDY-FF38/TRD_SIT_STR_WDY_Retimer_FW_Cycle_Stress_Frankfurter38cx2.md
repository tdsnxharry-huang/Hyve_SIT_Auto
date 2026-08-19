# Hyve Solutions

## Author
\<Harry Huang\>, \<SIT Team\>, harry.huang@hyvesolutions.com

## Reviewer
\<TBD\>

## Confidentiality
CONFIDENTIAL & PRIVILEGED.

This document contains confidential and privileged trade secrets and other information of Hyve Solutions and as such may not be disclosed to others not employed by Hyve Solutions. All rights reserved.

## Revision History

| Date | By | Description of changes |
|---|---|---|
| 2026/08/17 | Harry Huang | First release |

---

## 1. Test Information

**Test type**
- [x] Functional Test Case
- [ ] Regression Test Case

**System Under Test**
- [ ] x86 Intel System
- [x] x86 AMD System
- [ ] RISC ARM System

**Test Case Development Engineer**
Name: Harry Huang

**Project**
- [x] Frankfurter38cx2

---

## 2. Test Summary

### Results
Conclusion of the test: **PASS / FAIL**

### Criteria description

**PASS:**
- Necessary utilities installed (`ssh`, `scp`, `nitro-bmc`, `ping`).
- Necessary FW binary images exist (`upgrade-bin`, `downgrade-bin`).
- BMC is pingable and SSH is accessible.
- `flash_retimer_fw.sh` is successfully pushed to the BMC.
- Selected retimer card(s) are accessible via `retimer_app --get-fw-version`.
- Initial DOWNGRADE baseline flash succeeds and version is verified.
- Each upgrade cycle: FW flashed to upgrade version and version confirmed.
- Each downgrade cycle: FW flashed to downgrade version and version confirmed.
- DC-cycle checkpoint (every N cycles): power off → confirmed off → power on → confirmed on → BMC reachable → FW version matches expected.
- All cycles complete with no version mismatch.

**FAIL:**
- One of the above PASS criteria is not met.

---

## 3. Background

### Purpose and Scope of the Test
The purpose is to run Retimer FW upgrade/downgrade endurance (cycle) stress test to verify that the retimer FW flash is stable across repeated flashing cycles and DC power cycles.

### DC Cycle Command
```
nitro-bmc -i <BMC_IP> power off
→ wait for power status == off
nitro-bmc -i <BMC_IP> power on
→ wait for power status == on
→ wait for BMC ping response
→ wait for `nitro-bmc bmc info` to succeed
→ sleep <post_ac_settle_s> seconds
```

### Test Flow

**Initial Stage:**
1. Check BMC reachability (ping + SSH).
2. Push `flash_retimer_fw.sh` to BMC if not already present.
3. Verify selected retimer card(s) accessible via `retimer_app --get-fw-version`.
4. Flash DOWNGRADE FW baseline on all selected cards and verify version.

**Testing Stage (repeats until `--cycles` is reached):**
1. Flash UPGRADE FW on all cards → verify version via `retimer_app`.
2. Flash DOWNGRADE FW on all cards → verify version via `retimer_app`.
3. Every `--checkpoint-interval` cycles: DC-cycle the DUT, wait for recovery, verify FW version loaded correctly.

### Retimer Card Mapping (Frankfurter38cx2)

| Card Name | Label | I2C Bus | Device |
|---|---|---|---|
| `retimer1` | Cordite 0 | 53 | 35 |
| `retimer2` | Cordite 1 | 45 | 35 |

### Categories and Test Items

| Category | Test Item Name | Test Item Command | Description |
|---|---|---|---|
| **FW Flash (per cycle)** | upgrade | `flash_retimer_fw.sh <bus> <upgrade.bin>` (run on BMC via SSH) | Flash upgrade FW image via I2C on BMC |
| | upgrade_verify | `retimer_app --bus <bus> --device 35 --vendor astera --get-fw-version` | Verify version == upgrade version |
| | downgrade | `flash_retimer_fw.sh <bus> <downgrade.bin>` | Flash downgrade FW image |
| | downgrade_verify | `retimer_app --bus <bus> --device 35 --vendor astera --get-fw-version` | Verify version == downgrade version |
| **DC Checkpoint** (every N cycles) | dc_cycle_check | `nitro-bmc power off/on` + version verify | DC power cycle and verify FW version persists |

---

## 4. Additional Information

### Test Script Syntax

```
Usage:
  uv run --no-sync -- python retimer_fw_cycle_stress.py [OPTIONS]

  python retimer_fw_cycle_stress.py \
      --bmc-ip <BMC_IP> \
      [--cards retimer1,retimer2] \
      [--cycles 100] \
      [--checkpoint-interval 10] \
      [--upgrade-bin <path>] \
      [--upgrade-version 2.13.9] \
      [--downgrade-bin <path>] \
      [--downgrade-version 2.13.0] \
      [--log-dir <path>] \
      [--post-ac-settle-s 90] \
      [--power-status-timeout 180] \
      [--bmc-back-timeout 180] \
      [--continue-on-failure]

Parameters:
  --bmc-ip               : Nitro BMC IP address of the DUT (required)
  --cards                : Comma-separated cards to test: retimer1 (Cordite 0, bus 53),
                           retimer2 (Cordite 1, bus 45). Default: retimer1,retimer2
  --cycles               : Total upgrade/downgrade cycles. Default: 100
  --checkpoint-interval  : Run a DC-cycle checkpoint every N cycles. Default: 10
  --upgrade-bin          : FW binary image for the upgrade step.
                           Default: bin_file/pt516_x16_normal_hyve__SRNS__2.13_Ryan__v2_13_9.bin
  --upgrade-version      : Expected version string for upgrade image. Default: 2.13.9
  --downgrade-bin        : FW binary image for the downgrade step.
                           Default: bin_file/pt516_x16_normal_SRNS_v2_13_0.bin
  --downgrade-version    : Expected version string for downgrade image. Default: 2.13.0
  --log-dir              : Output directory for CSV/JSON results.
                           Default: logs/retimer_stress/<timestamp>
  --post-ac-settle-s     : Seconds to wait after power on before reading FW version. Default: 90
  --power-status-timeout : Seconds to wait for power status == on/off. Default: 180
  --bmc-back-timeout     : Seconds to wait for BMC ping/SSH to come back. Default: 180
  --continue-on-failure  : Keep looping after a failed step instead of aborting.
```

### Required Packages / Dependencies

```
Host side:
  - ssh / scp (openssh-client)
  - nitro-bmc CLI (nitro-bmc-cli package)
  - ping (iputils)
  - uv (Python package manager, https://docs.astral.sh/uv/)
  - Python 3.11+

BMC side (pushed automatically by the script):
  - scripts/bmc/flash_retimer_fw.sh
  - retimer_app (pre-installed in /var/env/halon-bmc/loaded/bin/)
```

### Example

```bash
# Run 100 cycles (default), DC checkpoint every 10 cycles, both retimer cards
./run_retimer_stress.sh --bmc-ip 10.0.0.5

# Custom: 200 cycles, checkpoint every 20, only Cordite 0
./run_retimer_stress.sh --bmc-ip 10.0.0.5 --cards retimer1 --cycles 200 --checkpoint-interval 20

# Continue on failure (don't abort on first fail)
./run_retimer_stress.sh --bmc-ip 10.0.0.5 --continue-on-failure
```

### Output Files

| File Name | Description |
|---|---|
| `logs/retimer_stress/<timestamp>/retimer_stress_results.csv` | Per-step result log (cycle, card, step, expected version, actual version, status, duration, timestamp) |
| `logs/retimer_stress/<timestamp>/retimer_stress_summary.json` | Final summary: completed cycles, pass/fail count per card, first failure info, overall_pass |

### CSV Column Description

| Column | Description |
|---|---|
| `cycle` | Cycle number (0 = initial baseline) |
| `card` | Card name (`retimer1` / `retimer2`) |
| `step` | Step name (`init_downgrade`, `upgrade`, `downgrade`, `dc_cycle_check`) |
| `expected_version` | Expected FW version string |
| `actual_version` | Actual FW version string read from `retimer_app` |
| `status` | `PASS` or `FAIL` |
| `duration_s` | Step duration in seconds |
| `timestamp` | ISO 8601 timestamp |
| `detail` | Additional detail (error message if failed) |

### Experience Required
Knowledge of BMC SSH access, retimer FW flashing, and nitro-bmc CLI operation.

### Test Items / Equipment Needed
- Frankfurter38cx2 DUT with Nitro BMC
- 2x Cordite K2V5 cards (retimer1 / retimer2) installed
- Host with SSH access to BMC
- Two Astera Labs PT516 FW binary images (upgrade + downgrade versions)

### Estimated Test Time

| Configuration | Estimated Time |
|---|---|
| 100 cycles, both cards, checkpoint every 10 | ~3–4 hours |
| 100 cycles, 1 card only | ~1.5–2 hours |
| Per cycle (flash×2 + verify×2 per card) | ~2–3 minutes |
| Per DC checkpoint | ~5–8 minutes (power cycle + settle + version check) |

---

## 5. Test Cases

### Instruction

#### Workflow

```
[Start]
  │
  ▼
[Preflight Check]
  Check: ssh, scp, nitro-bmc, ping exist
  Check: FW binary files exist
  Check: flash_retimer_fw.sh exists
  │
  ▼
[Initial Stage]
  BMC pingable? ──No──▶ [ABORT]
  SSH accessible? ──No──▶ setup_bmc_ssh (keg-install bootstrap)
  Push flash_retimer_fw.sh to BMC
  Read current retimer FW version (sanity check)
  Flash DOWNGRADE baseline on all cards
  Verify version == downgrade_version
  │
  ▼
[Testing Loop: cycle 1 to N]
  │
  ├─▶ Flash UPGRADE on all cards
  │     Verify version == upgrade_version
  │     FAIL? ──continue_on_failure=false──▶ [ABORT]
  │
  ├─▶ Flash DOWNGRADE on all cards
  │     Verify version == downgrade_version
  │     FAIL? ──continue_on_failure=false──▶ [ABORT]
  │
  └─▶ cycle % checkpoint_interval == 0?
        Yes ──▶ [DC Checkpoint]
                  nitro-bmc power off
                  Wait power status == off
                  nitro-bmc power on
                  Wait power status == on
                  Wait BMC ping
                  Wait `bmc info` success
                  Sleep post_ac_settle_s
                  Read FW version → verify == expected
                  FAIL? ──continue_on_failure=false──▶ [ABORT]
  │
  ▼
[End: Write summary JSON, exit 0=PASS / 1=FAIL]
```

---

### Test Case ID: `STR-RET-001`
**Test case name:** SIT_STR_WDY_Retimer_FW_Upgrade_Downgrade_Cycle_Frankfurter38cx2

**Description:** Retimer FW upgrade/downgrade endurance stress test — repeatedly flashes upgrade and downgrade FW images on Cordite K2V5 retimer cards and verifies the FW version after each flash.

**Applicable for:** FW Flash / Retimer stress

**Requirements:** Astera Labs PT516 retimer accessible on I2C bus 53 (Cordite 0) and bus 45 (Cordite 1).

**Initial Conditions:**
- BMC is alive and pingable.
- Host has SSH key access to BMC (or keg-install bootstrap is available).
- `bin_file/` contains both upgrade and downgrade FW binaries.

**Test Steps:**

| Step | Action | Expected Result |
|---|---|---|
| 1 | Run preflight check | All required tools present; FW binaries exist |
| 2 | Verify BMC SSH access | SSH connects to BMC as root |
| 3 | Push `flash_retimer_fw.sh` to BMC `/tmp/` | SCP succeeds |
| 4 | Read initial retimer FW version | Version string returned without error |
| 5 | Flash DOWNGRADE FW baseline | Flash succeeds; version == `downgrade_version` |
| 6 (per cycle) | Flash UPGRADE FW | Flash succeeds; version == `upgrade_version` |
| 7 (per cycle) | Flash DOWNGRADE FW | Flash succeeds; version == `downgrade_version` |
| 8 (every N cycles) | DC-cycle DUT | Power off confirmed → power on confirmed → BMC recovers → FW version == expected |
| 9 | Repeat steps 6–8 for all cycles | All cycles complete with PASS status |

**Pass Criteria:**
- `overall_pass: true` in summary JSON.
- All `status` fields in CSV == `PASS`.
- Zero failed cycles across both cards.

**Fail Criteria:**
- Any `status` == `FAIL` in CSV.
- `overall_pass: false` in summary JSON.
- Script exits with non-zero return code.
