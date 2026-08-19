# Retimer Flash Stress Minimal Bundle

## Changelog

| Date | Description |
|---|---|
| 2026-08-19 | **SSH 相容性**：統一所有 SSH/SCP 指令的 `-o` 選項（`BatchMode`, `StrictHostKeyChecking`, `UserKnownHostsFile`, `ConnectTimeout`），修正 BMC reboot 後 host key 改變導致 SSH 卡住的問題 |
| 2026-08-19 | **DC cycle 命名**：將程式中殘留的 `ac_cycle_checkpoint`、`--post-ac-settle-s` 等變數名稱統一改為 `dc_` 前綴，避免與 AC cycle（SNMP PDU）混淆 |
| 2026-08-17 | **電源控制改為 DC cycle**：將 `power cycle` 指令改為 `power off` + 確認 off + `power on` + 確認 on，中間穿插 power status 檢查，確保每步都有驗證 |
| 2026-08-17 | **初始版本**：從 frankfurter38cx2-bft 主包拆出獨立的 retimer FW upgrade/downgrade 壓力測試腳本，支援 DC cycle checkpoint、CSV/JSON 記錄、BMC SSH 自動 bootstrap |

This folder is a standalone minimal package for the **retimer FW flash stress** item only.

## Scope
- Keep client-server architecture.
  - Server: `retimer_fw_cycle_stress.py` (runs on automation host)
  - Client: `scripts/bmc/flash_retimer_fw.sh` (copied to DUT BMC and executed via SSH)
- Default test target is **100 cycles**.
- No manual SCP is required. Server checks whether the client script exists on DUT and uploads only when missing.

## Required files in this bundle
- `retimer_fw_cycle_stress.py`
- `run_retimer_stress.sh`
- `scripts/bmc/flash_retimer_fw.sh`
- `libs/client.py`
- `libs/helpers.py`
- `libs/frugen.py`
- `libs/fru_parser.py`
- `libs/__init__.py`
- `pyproject.toml`
- `keg-install/` subset for SSH bootstrap

## You still need to provide FW images
Place FW binaries at runtime and pass paths with args, or keep original default names.

Recommended explicit args:
- `--upgrade-bin <path-to-upgrade-bin>`
- `--downgrade-bin <path-to-downgrade-bin>`
- `--upgrade-version <version>`
- `--downgrade-version <version>`

## Host prerequisites (automation host)
- `python` 3.12+
- `ssh`, `scp` (OpenSSH client)
- `ping`
- `nitro-bmc` CLI

`uv` is auto-installed by `run_retimer_stress.sh` when missing.
Supported package managers for auto-install path:
- Ubuntu/Debian: `apt-get`
- CentOS/Rocky: `dnf` or `yum`

If BMC SSH key is not set up yet, also required:
- `coap`
- `bash`
- Files in `keg-install/`

## BMC prerequisites (DUT side)
- `/var/env/halon-bmc/loaded/bin/retimer_app`
- `flock` (util-linux)
- `i2cset`, `i2cget` (i2c-tools)
- `gpiodetect`, `gpioset` (libgpiod-tools)
- `awk`, `sed`, `dd`

## Install Python dependencies
```bash
uv sync
```

## Run (100 cycles)
```bash
./run_retimer_stress.sh --bmc-ip <BMC_IP> --cycles 100
```

## Runtime missing-install提示
- `run_retimer_stress.sh` checks current folder and auto-installs `uv` if missing.
- `retimer_fw_cycle_stress.py` checks host tools/files and exits with install hints if missing.
- `scripts/bmc/flash_retimer_fw.sh` checks BMC tools and exits with package hints if missing.

## Output
- `logs/retimer_stress/<timestamp>/retimer_stress_results.csv`
- `logs/retimer_stress/<timestamp>/retimer_stress_summary.json`
