#!/usr/bin/env python3
"""Client for the Nitro BMC."""

from __future__ import annotations

import subprocess
import re
from pathlib import Path
import time
from libs.helpers import (
    execute,
    ssh_run,
    ssh_check,
    setup_bmc_ssh,
    setup_droplet_ssh,
    scp_to,
)
from libs.frugen import write_fru

_CORDITE_BMC_SCRIPTS: tuple[str, ...] = (
    "scripts/bmc/flash_retimer_fw.sh",
    "scripts/bmc/uart_control.sh",
    "scripts/bmc/check_i2c_u42.sh",
    "scripts/bmc/check_i2c_mux_u25.sh",
    "scripts/bmc/check_i2c_u12.sh",
    "scripts/bmc/check_i2c_u364.sh",
    "scripts/bmc/check_i2c_mux_u67.sh",
    "scripts/bmc/check_voltage_reading.sh",
    "scripts/bmc/check_card_present.sh",
    "scripts/bmc/check_retimer_link_status.sh",
    "scripts/bmc/retimer_full_status.lua",
    "scripts/bmc/check_retimer_link_status.sh",
)

_CORDITE_DVD_SCRIPTS: tuple[str, ...] = (
    "scripts/dvd/set_i2c_mux.sh",
    "scripts/dvd/check_i2c_mux_u81.sh",
    "scripts/dvd/check_bridge_u65.sh",
    "scripts/dvd/check_external.sh",
)


class NitroBMC:
    def __init__(self, ip: str):
        self.ip = ip
        self.username = "admin"
        self.password = "admin"

    def run(self, command: str) -> subprocess.CompletedProcess[str]:
        return execute(f"nitro-bmc -i {self.ip} {command}")

    def dump_fru(self, fru_id: str) -> Path:
        cmd = f"fru readraw --fru-id {fru_id} --out-file fru{fru_id}.bin"
        self.run(cmd)
        fru_bin = Path(f"fru{fru_id}.bin")
        if not fru_bin.is_file():
            raise RuntimeError(f"Failed to dump FRU data from the BMC")
        return fru_bin

    def write_fru(self, fru_id: str, fru_bin: Path, uut_sn: str) -> None:

        write_fru(fru_id, self.ip, fru_bin, uut_sn)

    def reboot(self) -> subprocess.CompletedProcess[str]:
        return self.run("bmc reboot")

    def ping(self) -> subprocess.CompletedProcess[str]:
        cmd = ["ping", "-c5", "-W1", "-s", "1024", self.ip]
        return execute(cmd)

    def check_power_status(self) -> str:
        p = self.run("power status")
        match = re.search(r"Power state:   (\w+)", p.stdout)
        if match:
            return match.group(1)
        else:
            raise RuntimeError(f"Failed to check power status")

    def wait_for_power_status(self, status: str, timeout: int = 10) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.check_power_status() == status:
                return True
            time.sleep(1)
        raise TimeoutError(
            f"Failed to wait for power status {status} after {timeout} seconds"
        )

    def power_on(self) -> bool:
        self.run("power on")
        return self.wait_for_power_status("on")

    def power_off(self) -> bool:
        self.run("power off")
        return self.wait_for_power_status("off")

    def wait_for_command_success(self, command: str, timeout: int = 30) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.run(command).returncode == 0:
                return True
            time.sleep(1)
        raise TimeoutError(
            f"Failed to wait for command {command} to succeed after {timeout} seconds"
        )

    def setup_ssh_and_scp_scripts(self) -> None:
        if not ssh_check(self.ip):
            setup_bmc_ssh(self.ip)
        for path in _CORDITE_BMC_SCRIPTS:
            scp_to(self.ip, path)

    def ssh(self, command: str) -> subprocess.CompletedProcess[str]:
        if not ssh_check(self.ip):
            self.setup_ssh_and_scp_scripts()
        return ssh_run(command, self.ip, "root")

    def retimer1_fw_version(self) -> str:
        cmd = "/var/env/halon-bmc/loaded/bin/retimer_app --bus 53 --device 35 --vendor astera --get-fw-version"
        p = self.ssh(cmd)
        assert p.returncode == 0
        return p.stdout

    def retimer2_fw_version(self) -> str:
        cmd = "/var/env/halon-bmc/loaded/bin/retimer_app --bus 45 --device 35 --vendor astera --get-fw-version"
        p = self.ssh(cmd)
        assert p.returncode == 0
        return p.stdout


class Droplet:
    def __init__(self, ip: str):
        self.ip = ip

    def setup_ssh_and_scp_scripts(self) -> None:
        if not ssh_check(self.ip):
            setup_droplet_ssh(self.ip)
        for path in _CORDITE_DVD_SCRIPTS:
            scp_to(self.ip, path)

    def ssh(self, command: str) -> subprocess.CompletedProcess[str]:
        if not ssh_check(self.ip):
            self.setup_ssh_and_scp_scripts()
        return ssh_run(command, self.ip, "root")
