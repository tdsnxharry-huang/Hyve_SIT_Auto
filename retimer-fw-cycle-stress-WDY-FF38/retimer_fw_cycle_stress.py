#!/usr/bin/env python3
"""Retimer FW upgrade/downgrade endurance (cycle) stress test.

Client-server design (reuses the existing hyve-bft plumbing):
  * Server = this script, run on the automation host. It decides what to
        flash next, pushes only the required FW image + flash helper script to the
    BMC (SCP), triggers the flash over SSH (the BMC does the actual I2C
    flashing + FW-version check locally via retimer_app), then records the
    reported pass/fail result.
  * Client = the DUT's Nitro BMC. All I2C access is done through
        scripts/bmc/flash_retimer_fw.sh which is pushed on demand by this script.

Test flow:
  Initial stage:
    1. Confirm both FW images exist locally and the BMC/retimer(s) are
       reachable (SSH + `retimer_app --get-fw-version`).
    2. Flash the DOWNGRADE FW on every selected card and verify the version
       to establish a known-good baseline before the loop starts.
  Testing stage (runs until --cycles is reached):
    3. Flash the UPGRADE FW, check version.
    4. Flash the DOWNGRADE FW, check version.
       (3=>4 is one "cycle"; same as the BFT retimer flash test, just looped)
    5. Every --checkpoint-interval cycles: DC-cycle the DUT
       (`nitro-bmc -i $BMC_IP power off` followed by `power on`), wait for the BMC/host to come
       back, then verify the retimer FW version loaded correctly across the
       power cycle.
    6. Repeat 3=>4 (with checkpoints) until --cycles total cycles are done.
  Both K2V5 cards (Cordite 0 = retimer1 / bus 53, Cordite 1 = retimer2 /
  bus 45) are exercised every cycle by default, since a single DC cycle
  power-cycles the whole DUT (and therefore both cards) at once.

Results are written to CSV (every row) and a JSON summary (at the end / on
exit) under --log-dir.

Usage example:
  uv run --no-sync -- python retimer_fw_cycle_stress.py \\
      --bmc-ip 10.0.0.5 --cycles 100 --checkpoint-interval 10
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from libs.client import NitroBMC  # noqa: E402
from libs.helpers import (  # noqa: E402
    ping_ip_until_response,
    scp_to,
    setup_bmc_ssh,
    ssh_check,
)

CARD_BUS: dict[str, str] = {"retimer1": "53", "retimer2": "45"}
CARD_LABEL: dict[str, str] = {"retimer1": "Cordite 0", "retimer2": "Cordite 1"}
_VERSION_RE = re.compile(r'"Version"\s*:\s*"([^"]*)"')
FLASH_SCRIPT = REPO_ROOT / "scripts" / "bmc" / "flash_retimer_fw.sh"


def _require_cmd(cmd: str, install_hint: str) -> str | None:
    if shutil.which(cmd):
        return None
    return f"- {cmd}: install {install_hint}"


def preflight_requirements(bmc_ip: str) -> None:
    """Validate host-side prerequisites before starting stress cycles."""
    missing: list[str] = []

    for cmd, hint in (
        ("ssh", "openssh-client"),
        ("scp", "openssh-client"),
        ("nitro-bmc", "nitro-bmc CLI"),
        ("ping", "iputils / system ping"),
    ):
        miss = _require_cmd(cmd, hint)
        if miss:
            missing.append(miss)

    if not FLASH_SCRIPT.is_file():
        missing.append(f"- required script not found: {FLASH_SCRIPT}")

    # If key-based SSH is not ready, we will bootstrap via coap + keg-install.
    if not ssh_check(bmc_ip):
        for cmd, hint in (("coap", "coap client"), ("bash", "bash")):
            miss = _require_cmd(cmd, hint)
            if miss:
                missing.append(miss)
        keg_dir = REPO_ROOT / "keg-install"
        for rel in (
            "keg-install",
            "put_pub_key.sh",
            "mfg-public-key.pem",
            "nitro-bmc-mfg-0.3940.0.keg",
            "carbon-ndk-ast2500-0.204527.0.keg",
        ):
            required = keg_dir / rel
            if not required.exists():
                missing.append(f"- missing bootstrap file: {required}")

    if missing:
        sys.exit(
            "Error: missing prerequisites for retimer stress:\n"
            + "\n".join(missing)
            + "\nHint: install missing tools first, then run setup_env.sh / uv sync."
        )


@dataclass
class FwImage:
    bin_file: Path
    version: str


@dataclass
class CycleResult:
    cycle: int
    card: str
    step: str
    expected_version: str
    actual_version: str
    status: str
    duration_s: float
    timestamp: str
    detail: str = ""


class ResultLog:
    """Streams every step to CSV and produces a JSON summary at the end."""

    _FIELDS = (
        "cycle",
        "card",
        "step",
        "expected_version",
        "actual_version",
        "status",
        "duration_s",
        "timestamp",
        "detail",
    )

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.log_dir / "retimer_stress_results.csv"
        self._fh = self.csv_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=self._FIELDS)
        self._writer.writeheader()
        self.results: list[CycleResult] = []

    def add(self, r: CycleResult) -> None:
        self.results.append(r)
        self._writer.writerow(asdict(r))
        self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def write_summary(self, completed_cycles: int, total_cycles: int) -> dict:
        summary: dict = {
            "generated_at": datetime.now().isoformat(),
            "completed_cycles": completed_cycles,
            "total_cycles": total_cycles,
            "cards": {},
        }
        cards = sorted({r.card for r in self.results})
        for card in cards:
            card_results = [r for r in self.results if r.card == card]
            failed = [r for r in card_results if r.status != "PASS"]
            summary["cards"][card] = {
                "label": CARD_LABEL.get(card, card),
                "total_steps": len(card_results),
                "passed": len(card_results) - len(failed),
                "failed": len(failed),
                "first_failure": (
                    {"cycle": failed[0].cycle, "step": failed[0].step}
                    if failed
                    else None
                ),
            }
        summary["overall_pass"] = (
            completed_cycles == total_cycles
            and all(c["failed"] == 0 for c in summary["cards"].values())
        )
        path = self.log_dir / "retimer_stress_summary.json"
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary


def parse_fw_version(fw_json_text: str) -> str:
    """Extract the "Version" field from retimer_app --get-fw-version output."""
    match = _VERSION_RE.search(fw_json_text)
    return match.group(1) if match else fw_json_text.strip()


def read_retimer_version(bmc: NitroBMC, card: str) -> str:
    getter = getattr(bmc, f"{card}_fw_version")
    return parse_fw_version(getter())


def ensure_client_flash_script(bmc: NitroBMC) -> None:
    """Check client script on DUT and upload only when it is missing."""
    if getattr(bmc, "_flash_script_ready", False):
        return

    check = bmc.ssh("test -x flash_retimer_fw.sh")
    if check.returncode == 0:
        setattr(bmc, "_flash_script_ready", True)
        return

    scp_to(bmc.ip, str(FLASH_SCRIPT))
    p = bmc.ssh("chmod +x flash_retimer_fw.sh")
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        raise RuntimeError(f"failed to prepare flash_retimer_fw.sh on DUT: {err}")
    setattr(bmc, "_flash_script_ready", True)


def flash_and_verify(
    bmc: NitroBMC,
    card: str,
    image: FwImage,
    cycle: int,
    step: str,
    log: ResultLog,
) -> bool:
    """Push the FW image + run flash_retimer_fw.sh on the BMC, then verify."""
    t0 = time.time()
    ensure_client_flash_script(bmc)
    scp_to(bmc.ip, str(image.bin_file))
    remote_name = os.path.basename(image.bin_file)
    cmd = f"sh flash_retimer_fw.sh {remote_name} {card} {image.version}"
    p = bmc.ssh(cmd)
    flash_ok = p.returncode == 0

    actual_version = ""
    try:
        actual_version = read_retimer_version(bmc, card)
    except Exception as exc:  # noqa: BLE001 - want to log and continue
        actual_version = f"<error: {exc}>"

    status = "PASS" if flash_ok and actual_version == image.version else "FAIL"
    detail = "" if status == "PASS" else ((p.stderr or p.stdout or "").strip()[-500:])

    log.add(
        CycleResult(
            cycle=cycle,
            card=card,
            step=step,
            expected_version=image.version,
            actual_version=actual_version,
            status=status,
            duration_s=round(time.time() - t0, 2),
            timestamp=datetime.now().isoformat(),
            detail=detail,
        )
    )
    label = CARD_LABEL.get(card, card)
    print(f"    [{label}/{card}] {step}: expected={image.version} actual={actual_version} -> {status}")
    return status == "PASS"


def ac_cycle_checkpoint(
    bmc: NitroBMC,
    cards: list[str],
    expected_versions: dict[str, str],
    cycle: int,
    log: ResultLog,
    settle_s: int,
    power_timeout: int,
    bmc_back_timeout: int,
) -> bool:
    """Power-cycle the DUT (DC cycle via power off/on), wait for it to come back, and re-check FW versions."""
    t0 = time.time()
    
    print(f"  [Checkpoint] Cycle {cycle}: issuing power off "
          f"(nitro-bmc -i {bmc.ip} power off) ...")
    bmc.run("power off")
    
    print("  [Checkpoint] Verifying power is off ...")
    bmc.wait_for_power_status("off", timeout=power_timeout)
    print("  [Checkpoint] Confirmed power is off.")
    
    print(f"  [Checkpoint] Issuing power on (nitro-bmc -i {bmc.ip} power on) ...")
    bmc.run("power on")
    
    print("  [Checkpoint] Verifying power is on ...")
    bmc.wait_for_power_status("on", timeout=power_timeout)
    print("  [Checkpoint] Confirmed power is on.")

    print("  [Checkpoint] Waiting for BMC to come back ...")
    ping_ip_until_response(bmc.ip, timeout=bmc_back_timeout)
    bmc.wait_for_command_success("bmc info", timeout=bmc_back_timeout)

    print(f"  [Checkpoint] Host power is on, settling {settle_s}s before FW read-back ...")
    time.sleep(settle_s)

    # After DC cycle, re-check script presence before the next flash.
    setattr(bmc, "_flash_script_ready", False)

    all_ok = True
    for card in cards:
        try:
            actual_version = read_retimer_version(bmc, card)
        except Exception as exc:  # noqa: BLE001
            actual_version = f"<error: {exc}>"
        expected = expected_versions[card]
        status = "PASS" if actual_version == expected else "FAIL"
        all_ok = all_ok and status == "PASS"
        log.add(
            CycleResult(
                cycle=cycle,
                card=card,
                step="dc_cycle_check",
                expected_version=expected,
                actual_version=actual_version,
                status=status,
                duration_s=round(time.time() - t0, 2),
                timestamp=datetime.now().isoformat(),
            )
        )
        label = CARD_LABEL.get(card, card)
        print(f"    [{label}/{card}] post-DC-cycle FW version: expected={expected} "
              f"actual={actual_version} -> {status}")
    return all_ok


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Retimer FW upgrade/downgrade endurance loop with periodic DC-cycle checkpoints.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--bmc-ip", default="", help="Nitro BMC IP of the DUT. Prompted if omitted.")
    p.add_argument(
        "--cards",
        default="retimer1,retimer2",
        help="Comma-separated cards to test: retimer1 (Cordite 0), retimer2 (Cordite 1).",
    )
    p.add_argument("--cycles", type=int, default=100, help="Total upgrade/downgrade cycles.")
    p.add_argument(
        "--checkpoint-interval",
        type=int,
        default=10,
        help="Run a DC-cycle checkpoint every N cycles.",
    )
    p.add_argument(
        "--upgrade-bin",
        default="bin_file/pt516_x16_normal_hyve__SRNS__2.13_Ryan__v2_13_9.bin",
        help="FW image used for the 'upgrade' step.",
    )
    p.add_argument("--upgrade-version", default="2.13.9", help="Target version for the upgrade image.")
    p.add_argument(
        "--downgrade-bin",
        default="bin_file/pt516_x16_normal_SRNS_v2_13_0.bin",
        help="FW image used for the 'downgrade' step.",
    )
    p.add_argument("--downgrade-version", default="2.13.0", help="Target version for the downgrade image.")
    p.add_argument("--log-dir", default="", help="Directory for CSV/JSON results (default: logs/retimer_stress/<timestamp>).")
    p.add_argument(
        "--post-ac-settle-s",
        type=int,
        default=90,
        help="Seconds to wait after host power is back on before reading retimer FW version.",
    )
    p.add_argument("--power-status-timeout", type=int, default=180, help="Seconds to wait for host 'power status' == on.")
    p.add_argument("--bmc-back-timeout", type=int, default=180, help="Seconds to wait for BMC ping/SSH to come back.")
    p.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Keep looping after a failed step instead of aborting immediately.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    bmc_ip = args.bmc_ip.strip() or input("Enter BMC IP address of the DUT: ").strip()
    if not bmc_ip:
        sys.exit("Error: BMC IP is required.")

    cards = [c.strip() for c in args.cards.split(",") if c.strip()]
    for card in cards:
        if card not in CARD_BUS:
            sys.exit(f"Error: unknown card '{card}', expected one of {sorted(CARD_BUS)}")
    if not cards:
        sys.exit("Error: no cards selected.")

    preflight_requirements(bmc_ip)

    upgrade = FwImage(Path(args.upgrade_bin), args.upgrade_version)
    downgrade = FwImage(Path(args.downgrade_bin), args.downgrade_version)
    for image in (upgrade, downgrade):
        if not image.bin_file.is_file():
            sys.exit(f"Error: FW image not found: {image.bin_file}")

    log_dir = (
        Path(args.log_dir)
        if args.log_dir
        else Path("logs") / "retimer_stress" / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    log = ResultLog(log_dir)
    print(f"Logging results to: {log_dir}")

    bmc = NitroBMC(bmc_ip)
    completed_cycles = 0

    def abort(reason: str) -> None:
        log.close()
        summary = log.write_summary(completed_cycles, args.cycles)
        print(json.dumps(summary, indent=2))
        sys.exit(f"ABORTED: {reason}")

    try:
        # ---------------- Initial stage ----------------
        print(f"[Initial] Checking BMC {bmc_ip} reachability ...")
        if not ping_ip_until_response(bmc_ip, timeout=60):
            abort(f"BMC {bmc_ip} is not pingable")

        if not ssh_check(bmc_ip):
            print("[Initial] SSH key not ready, bootstrapping BMC SSH access via keg-install ...")
            setup_bmc_ssh(bmc_ip)

        if not ssh_check(bmc_ip):
            abort(f"BMC {bmc_ip} SSH is not available")

        try:
            ensure_client_flash_script(bmc)
        except Exception as exc:  # noqa: BLE001
            abort(f"Failed to prepare flash_retimer_fw.sh on BMC: {exc}")

        print("[Initial] Verifying selected retimer(s) are accessible ...")
        for card in cards:
            try:
                version = read_retimer_version(bmc, card)
            except Exception as exc:  # noqa: BLE001
                abort(f"Cannot read FW version for {card} ({CARD_LABEL[card]}): {exc}")
            print(f"  {CARD_LABEL[card]} ({card}, bus {CARD_BUS[card]}) current FW version: {version}")

        print(f"[Initial] Flashing DOWNGRADE baseline ({downgrade.version}) on: "
              f"{', '.join(CARD_LABEL[c] for c in cards)} ...")
        expected: dict[str, str] = {}
        for card in cards:
            ok = flash_and_verify(bmc, card, downgrade, cycle=0, step="init_downgrade", log=log)
            expected[card] = downgrade.version if ok else "unknown"
            if not ok and not args.continue_on_failure:
                abort(f"Initial downgrade baseline failed on {card}")

        # ---------------- Testing stage ----------------
        print(f"[Testing] Starting {args.cycles} upgrade/downgrade cycles "
              f"(DC-cycle checkpoint every {args.checkpoint_interval} cycles) ...")

        for cycle in range(1, args.cycles + 1):
            print(f"-- Cycle {cycle}/{args.cycles} --")
            cycle_ok = True

            for card in cards:
                ok = flash_and_verify(bmc, card, upgrade, cycle, "upgrade", log)
                if ok:
                    expected[card] = upgrade.version
                cycle_ok = cycle_ok and ok
                if not ok and not args.continue_on_failure:
                    abort(f"Cycle {cycle} upgrade failed on {card}")

            for card in cards:
                ok = flash_and_verify(bmc, card, downgrade, cycle, "downgrade", log)
                if ok:
                    expected[card] = downgrade.version
                cycle_ok = cycle_ok and ok
                if not ok and not args.continue_on_failure:
                    abort(f"Cycle {cycle} downgrade failed on {card}")

            completed_cycles = cycle

            if cycle % args.checkpoint_interval == 0:
                ok = ac_cycle_checkpoint(
                    bmc,
                    cards,
                    expected,
                    cycle,
                    log,
                    settle_s=args.post_ac_settle_s,
                    power_timeout=args.power_status_timeout,
                    bmc_back_timeout=args.bmc_back_timeout,
                )
                if not ok and not args.continue_on_failure:
                    abort(f"DC-cycle checkpoint failed at cycle {cycle}")

        log.close()
        summary = log.write_summary(completed_cycles, args.cycles)
        print(json.dumps(summary, indent=2))
        sys.exit(0 if summary["overall_pass"] else 1)

    except KeyboardInterrupt:
        print("\nInterrupted by user, saving partial results ...")
        abort("Interrupted by user")


if __name__ == "__main__":
    main()
