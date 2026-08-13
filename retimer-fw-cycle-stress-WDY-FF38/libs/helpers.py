"""Hopper49-specific helper functions adapted for the hyve-bft framework.

These helpers wrap platform-specific operations (nitro-bmc CLI, CoAP, SOL)
that are unique to the Hopper49 / Nitro BMC platform.
"""

from __future__ import annotations

import json
import re
import time
import sys
import contextlib
from typing import TYPE_CHECKING, Callable
from functools import wraps
import time
import pexpect
from loguru import logger

from libs.fru_parser import parse_fru

"""General-purpose utility functions migrated from lib/common.py and lib/utils.py."""

import json
import re
import subprocess
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Iterable, Union

from loguru import logger


# -- Command execution --------------------------------------------------------

def execute(command: Union[str, list[str]], check: bool = False, **options) -> subprocess.CompletedProcess:
    if isinstance(command, list):
        command = " ".join(command)
    logger.info(f"execute command: {command}")
    p = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        encoding="utf-8",
        universal_newlines=True,
        check=check,
        **options,
    )
    if p.stdout:
        logger.info(f"stdout:\n{p.stdout}")
    if p.stderr:
        logger.info(f"stderr:\n{p.stderr}")
    return p

def ssh_run(
    command: Union[str, list[str]], host: str, user: str, **options
) -> subprocess.CompletedProcess:
    ssh_command = (
        f'ssh -oBatchMode=yes -i ~/.ssh/id_ecdsa -oForwardAgent=yes '
        f'-oStrictHostKeyChecking=no {user}@{host} "{command}"'
    )
    logger.info(f"Executing SSH command: {ssh_command}")
    cp = subprocess.run(
        ssh_command, capture_output=True, shell=True, check=False, text=True, **options
    )
    if cp.stdout:
        logger.info(f"stdout:\n{cp.stdout}")
    if cp.stderr:
        logger.info(f"stderr:\n{cp.stderr}")
    return cp


# -- Retry decorator ----------------------------------------------------------

def retry(retries: int = 3, delay: float = 1, exceptions: tuple = (Exception,)):
    """Retry decorator with configurable retries, delay, and exception types."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for _ in range(retries):
                try:
                    return func(*args, **kwargs)
                except exceptions:
                    time.sleep(delay)
            return func(*args, **kwargs)
        return wrapper
    return decorator

# timeout decorator
def timeout(seconds: int) -> Callable:
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            while time.time() - start_time < seconds:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    time.sleep(1)
            raise TimeoutError(f"Function {func.__name__} timed out after {seconds} seconds")
        return wrapper
    return decorator


def parse_i2cdetect(input_str: str) -> list[str]:
    """Parse i2cdetect output and return list of hex addresses."""
    addresses: list[int] = []
    pattern = r"^(\d{2}):(( \w{2}| --|   )+)"
    for line in input_str.split("\n"):
        match = re.search(pattern, line)
        if match:
            base = int(match.group(1), 16)
            addrs = match.group(2)
            pattern2 = r" (\w{2}|--|  )"
            matches = re.findall(pattern2, addrs)
            for offset, addr in enumerate(matches):
                if addr != "--" and addr != "  ":
                    addresses.append(base + int(offset))
    return [hex(item) for item in addresses]


# ---------------------------------------------------------------------------
# SOL (Serial-over-LAN) helpers
# ---------------------------------------------------------------------------

SOL_PROMPT_ACTIVATE_CONSOLE = "Please press Enter to activate this console."
SOL_PROMPT_MENU_CHOOSE = "Please choose from menu:"
SOL_PROMPT_MENU_SEQUENCE = "Please enter menu selection or sequence:"
SOL_MENU_PROMPTS = [SOL_PROMPT_MENU_CHOOSE, SOL_PROMPT_MENU_SEQUENCE]
SOL_UC_ALIVE_MESSAGE = "K2V5 DVD uC is alive"


def open_sol(bmc_ip: str, sol_id: int = 0, logfile: str | None = None):
    """Context manager that opens a nitro-bmc SOL session.

    Yields a ``pexpect`` child for interactive expect/send.
    """
    if logfile is None:
        logfile = f"sol_output_id{sol_id}.txt"
    deactivate_cmd = f"nitro-bmc -i {bmc_ip} sol deactivate -u admin -p admin -c"
    execute(deactivate_cmd)
    cmd = f"nitro-bmc -i {bmc_ip} sol activate -u admin -p admin -c -d{sol_id}"

    class _SolCtx:
        def __enter__(self):
            logger.info(f"open sol: {cmd}")
            self._child = pexpect.spawnu(cmd)
            self._fh = open(logfile, "a")
            self._child.logfile_read = self._fh
            return self._child

        def __exit__(self, *exc):
            self._fh.flush()
            self._fh.close()
            execute(deactivate_cmd)
            return False

    return _SolCtx()


# ---------------------------------------------------------------------------
# Boot / connectivity checks
# ---------------------------------------------------------------------------

def check_k2_boot_to_menu(bmc_ip: str, sol_id: int = 2, timeout: int = 600) -> bool:
    """Wait for K2 card to boot into its control menu via SOL."""
    logger.info(f"waiting K2 card (sol id{sol_id}) boot to menu")
    menu_prompts = [SOL_PROMPT_ACTIVATE_CONSOLE, *SOL_MENU_PROMPTS]
    poll_seconds = 30

    with open_sol(bmc_ip, sol_id) as sol:
        try:
            deadline = time.time() + timeout
            sol.sendline()
            sol.sendline()
            initial_wait = min(30.0, max(0.0, deadline - time.time()))
            try:
                sol.expect(r".+", timeout=initial_wait)
            except pexpect.exceptions.TIMEOUT:
                logger.info("No output from sol within 30 seconds")
                raise TimeoutError("No output from sol within 30 seconds")

            while time.time() < deadline:
                sol.sendline()
                sol.sendline()
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    sol.expect_exact(
                        menu_prompts,
                        timeout=min(poll_seconds, remaining),
                    )
                    return True
                except pexpect.exceptions.TIMEOUT:
                    logger.info(
                        "Menu prompt not seen in this poll window; "
                        "sending newlines again"
                    )
                    continue

            logger.info(f"Timeout waiting for prompt after {timeout} seconds")
            raise TimeoutError(f"Timeout waiting for prompt after {timeout} seconds")
        except Exception as e:
            logger.error(f"Error checking K2 card (sol id{sol_id}): {e}")
            raise e
        finally:
            logger.info(f"check K2 card (sol id{sol_id}) end")
    return True


def check_sol_in_uc_mode(bmc_ip: str, sol_id: int = 3, timeout: int = 60) -> bool:
    """Check if the SOL is in UC mode."""
    logger.info(f"checking if the SOL is in UC mode (sol id{sol_id})")
    with open_sol(bmc_ip, sol_id) as sol:
        try:
            sol.sendline()
            sol.sendline()
            sol.expect_exact(SOL_UC_ALIVE_MESSAGE, timeout=timeout)
            logger.info(f"SOL is in UC mode (sol id{sol_id})")
            return True
        except pexpect.exceptions.TIMEOUT:
            logger.error(f"SOL is not in UC mode (sol id{sol_id})")
            raise TimeoutError(f"SOL is not in UC mode (sol id{sol_id})")
        except Exception as e:
            logger.error(f"Error checking if the SOL is in UC mode (sol id{sol_id}): {e}")
            raise e
        finally:
            logger.info(f"check SOL in UC mode (sol id{sol_id}) end")


def check_dvd_boot_to_menu(bmc_ip: str, sol_id: int = 4, timeout: int = 600) -> bool:
    """Wait for DVD card to boot into its control menu via SOL id 4."""
    logger.info(f"waiting DVD card (sol id{sol_id}) boot to menu")
    menu_prompts = [SOL_PROMPT_ACTIVATE_CONSOLE, *SOL_MENU_PROMPTS]
    poll_seconds = 30

    with open_sol(bmc_ip, sol_id) as sol:
        try:
            deadline = time.time() + timeout
            sol.sendline()
            sol.sendline()
            initial_wait = min(30.0, max(0.0, deadline - time.time()))
            try:
                sol.expect(r".+", timeout=initial_wait)
            except pexpect.exceptions.TIMEOUT:
                logger.info("No output from sol within 30 seconds")
                raise TimeoutError("No output from sol within 30 seconds")

            while time.time() < deadline:
                sol.sendline()
                sol.sendline()
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    sol.expect_exact(
                        menu_prompts,
                        timeout=min(poll_seconds, remaining),
                    )
                    return True
                except pexpect.exceptions.TIMEOUT:
                    logger.info(
                        "Menu prompt not seen in this poll window; "
                        "sending newlines again"
                    )
                    continue

            logger.info(f"Timeout waiting for prompt after {timeout} seconds")
            raise TimeoutError(f"Timeout waiting for prompt after {timeout} seconds")
        except Exception as e:
            logger.error(f"Error checking DVD card (sol id{sol_id}): {e}")
            raise e
        finally:
            logger.info(f"check DVD card (sol id{sol_id}) end")
    return True


@retry(retries=30, delay=2)
def get_card_ip(bmc_ip: str, sol_id: int = 4) -> str:
    """Discover a card's IP by navigating its SOL menu (ifconfig)."""
    ifconfig_output = ""
    with open_sol(bmc_ip, sol_id) as sol:
        timeout_count = 0

        def _navigate():
            nonlocal timeout_count, ifconfig_output
            try:
                sol.sendline()
                sol.sendline()
                sol.expect_exact(SOL_PROMPT_ACTIVATE_CONSOLE, timeout=2)
                sol.sendline()
                sol.expect_exact(SOL_MENU_PROMPTS, timeout=2)
                sol.sendline("4")
                index = sol.expect_exact(
                    [*SOL_MENU_PROMPTS, SOL_PROMPT_ACTIVATE_CONSOLE], timeout=2
                )
                if index == 2:
                    sol.sendline()
                    sol.expect_exact(SOL_MENU_PROMPTS, timeout=2)
                    sol.sendline("4")
                    sol.expect_exact(SOL_MENU_PROMPTS, timeout=2)
                sol.sendline("5")
                sol.expect_exact(SOL_MENU_PROMPTS, timeout=2)
                sol.sendline("1")
                # After ifconfig, the card may return to the menu or the
                # "activate console" prompt; accept either. Allow extra time for
                # ifconfig output to arrive.
                sol.expect_exact(
                    [*SOL_MENU_PROMPTS, SOL_PROMPT_ACTIVATE_CONSOLE],
                    timeout=10,
                )
                ifconfig_output = sol.before
            except pexpect.exceptions.TIMEOUT:
                if re.search(r"inet addr:(169\.254\.0\.\d{1,3})", sol.before):
                    ifconfig_output = sol.before
                    return
                timeout_count += 1
                if timeout_count > 3:
                    raise
                _navigate()

        _navigate()

    if not ifconfig_output:
        ifconfig_output = sol.before
    match = re.search(r"inet addr:(169\.254\.0\.\d{1,3})", ifconfig_output)
    if match:
        return match.group(1)
    raise ValueError("Could not discover card IP from SOL ifconfig output")


def wait_for_os_login(bmc_ip: str, timeout: int = 300) -> bool:
    """Wait for the host OS to boot and login via SOL id 0."""
    start_time = time.time()    
    logger.info(f"waiting for the host OS to boot and login via SOL id 0")
    while time.time() - start_time < timeout:
        try:
            with open_sol(bmc_ip, 0) as sol:
                sol.sendline()    
                sol.expect_exact(["login: ", "[root@localhost ~]#"])
                logger.info(f"host OS is booted and logged in")
                return True
        except pexpect.exceptions.TIMEOUT:
            continue
    raise TimeoutError(f"Failed to wait for the host OS to boot and login via SOL id 0 after {timeout} seconds")


def execute_os_command(bmc_ip: str, command: str):
    """Execute a command on the host OS via SOL id 0."""
    logger.info(f"send {command} to OS")
    with open_sol(bmc_ip, 0) as sol:
        sol.sendline()
        index = sol.expect_exact(
            [r" login: ", r"Password: ", r" ~]# ", pexpect.EOF, pexpect.TIMEOUT]
        )
        if index == 0:
            sol.sendline("root")
            sol.expect_exact(r"Password: ")
            sol.sendline("123456")
            sol.expect_exact(r" ~]# ")
        elif index == 1:
            sol.sendline("123456")
            sol.expect_exact(r" ~]# ")
        elif index in (3, 4):
            return 1
        sol.sendline(command)
        sol.expect_exact(r" ~]# ")
        logger.info(f"output: {sol.before}")
        return sol.before


# ---------------------------------------------------------------------------
# BMC setup
# ---------------------------------------------------------------------------

def setup_bmc_ssh(bmc_ip: str) -> None:
    """Install SSH keys on the BMC so key-based auth works."""
    if ssh_check(bmc_ip):
        return
    cwd = "./keg-install"
    key = "mfg-public-key.pem"
    cmd = (
        f"coap -O65001,0 -Y coaps+tcp://{bmc_ip}/api-v1/keystore/appkey"
        f" -m PUT -f {key}"
    )
    retry(retries=10, delay=5)(execute)(cmd, cwd=cwd, check=True)
    keg = "nitro-bmc-mfg-0.3940.0.keg"
    cmd = f"bash keg-install --keg {keg} --upload-tool coap --ip {bmc_ip} --preserve-keg"
    retry(retries=10, delay=5)(execute)(cmd, cwd=cwd, check=True)
    keg = "carbon-ndk-ast2500-0.204527.0.keg"
    cmd = f"bash keg-install --keg {keg} --upload-tool coap --ip {bmc_ip} --preserve-keg"
    retry(retries=10, delay=5)(execute)(cmd, cwd=cwd, check=True)
    put_pub_key(bmc_ip, "/api-v1/debug/ndk/authorized_keys")


def setup_droplet_ssh(droplet_ip: str) -> None:
    """Install SSH keys on a droplet (JRD/DVD)."""
    if ssh_check(droplet_ip):
        return
    cwd = "./keg-install"
    keg = "carbon-ndk-aarch64-0.204240.0.keg_signed"
    cmd = f"bash keg-install --keg {keg} --upload-tool coap --ip {droplet_ip} --preserve-keg"
    retry(retries=10, delay=5)(execute)(cmd, cwd=cwd, check=True)
    put_pub_key(droplet_ip, "/api-v1/debug/ndk/authorized_keys")


def put_pub_key(ip: str, key_path: str) -> None:
    """Upload public key to a device at the given API path."""
    import subprocess, shlex
    cwd = "./keg-install"
    cmd = f"bash put_pub_key.sh {ip} {key_path}"
    p = subprocess.run(shlex.split(cmd), cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(f"'{cmd}' failed")


def ssh_check(ip: str, username: str = "root") -> bool:
    """Return True if SSH login succeeds."""
    if not ip:
        return False
    cmd = (
        f"ssh -o BatchMode=yes -i ~/.ssh/id_ecdsa "
        f"-oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no "
        f"{username}@{ip} uptime"
    )
    p = execute(cmd)
    return p.returncode == 0


def ssh_execute(ip: str, remote_cmd: str, **options):
    """Run a command on a remote host via SSH."""
    cmd = (
        f"ssh -o BatchMode=yes -i ~/.ssh/id_ecdsa "
        f"-oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no "
        f"root@{ip} {remote_cmd}"
    )
    return execute(cmd, **options)


def scp_to(ip: str, filename: str):
    """SCP a file to a remote host's home directory."""
    cmd = (
        f"scp -i ~/.ssh/id_ecdsa -oUserKnownHostsFile=/dev/null "
        f"-oStrictHostKeyChecking=no {filename} root@{ip}:~/"
    )
    return execute(cmd)


def scp_from(ip: str, filename: str):
    """SCP a file from a remote host's home directory."""
    cmd = (
        f"scp -i ~/.ssh/id_ecdsa -oUserKnownHostsFile=/dev/null "
        f"-oStrictHostKeyChecking=no root@{ip}:~/{filename} ."
    )
    return execute(cmd)

# ---------------------------------------------------------------------------
# K2V5 helpers
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Power control (nitro-bmc based)
# ---------------------------------------------------------------------------

@retry(retries=10, delay=1)
def power_on_with_retry(bmc_ip: str) -> None:
    """Power on the system via nitro-bmc with retries."""
    execute(f"nitro-bmc -i {bmc_ip} power on", check=True)
    p = execute(f"nitro-bmc -i {bmc_ip} power status", check=True)
    assert "Power state:   on" in p.stdout


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------

def ping_ip_until_response(ip: str, timeout: int = 300) -> bool:
    """Ping an IP until it responds or the timeout is reached."""
    start_time = time.time()
    logger.info(f"Pinging {ip} until it responds or the timeout is reached.")
    logger.info(f"Timeout: {timeout}s")
    while True:
        try:
            result = execute(f"ping -c 10 {ip}")
            if result.returncode == 0:
                logger.info(f"IP {ip} responded.")
                return True
            logger.info(f"IP {ip} did not respond. Retrying...")
            if time.time() - start_time >= timeout:
                logger.info(f"Timeout of {timeout}s reached. {ip} did not respond.")
                raise TimeoutError(f"Timeout of {timeout}s reached. {ip} did not respond.")
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Ping interrupted by user.")
            raise KeyboardInterrupt
        except Exception as e:
            logger.info(f"Ping error: {e}")
            raise e

def sleep(seconds: int) -> None:
    logger.info(f"sleeping for {seconds} seconds")
    time.sleep(seconds)
    logger.info(f"slept for {seconds} seconds")

# ---------------------------------------------------------------------------
# I2C / CoAP helpers
# ---------------------------------------------------------------------------

def coap_to_json(ip_addr: str, path: str) -> dict | None:
    """Execute a CoAP GET and return parsed JSON."""
    cmd = f"coap -O65001,0 -J coaps+tcp://{ip_addr}{path}"
    p = execute(cmd)
    if p.returncode != 0:
        return None
    return json.loads(p.stdout)


def i2c_check(ip: str, bus: str, addr_hex: str) -> str:
    """Check that an I2C device is present and readable."""
    cmd = f"i2cget -fy {bus} {addr_hex} 0x00"
    p = ssh_execute(ip, cmd)
    assert p.returncode == 0, f"i2cget on {bus}/{addr_hex} failed"
    data = p.stdout.strip()
    assert data != "", f"Empty response from {bus}/{addr_hex}"
    assert data != "0x00", f"Zero response from {bus}/{addr_hex}"
    cmd = f"i2cdetect -r -y {bus}"
    p = ssh_execute(ip, cmd)
    assert p.returncode == 0
    available = parse_i2cdetect(p.stdout)
    assert addr_hex in available, f"{addr_hex} not on bus {bus}"
    return data


def check_addr_in_i2c_bus(ip: str, bus, addr: str) -> bool:
    """Check if an I2C address is present on a given bus."""
    cmd = f"i2cdetect -r -y {bus}"
    p = ssh_execute(ip, cmd)
    if p.returncode != 0:
        return False
    available = parse_i2cdetect(p.stdout)
    return addr in available


def get_fw_info(ip: str) -> None:
    """Log firmware information from a device via CoAP."""
    logger.info(f"FW info of: {ip}")
    data1 = coap_to_json(ip, "/api-v1/firmware-version/local/running")
    data2 = coap_to_json(ip, "/api-v1/healthcheck/boot")
    if data1:
        logger.info(json.dumps(data1, indent=2))
    if data2:
        logger.info(json.dumps(data2.get("version"), indent=2))
    for fw in ("NX-PP", "Carbon", "K2", "CarbonAPI", "FirmwareImages"):
        data = coap_to_json(ip, f"/api-v1/packages/{fw}")
        if data:
            logger.info(f"{data['name']}: {data['running']['version']}")


def get_bios_bmc_version(bmc_ip: str) -> None:
    """Log BMC and BIOS version info via CoAP."""
    logger.info("get BMC information")
    coap_to_json(bmc_ip, "/api-v2/bmc/info")
    logger.info("get BIOS information")
    coap_to_json(bmc_ip, "/api-v2/bmc/host/info")



# ---------------------------------------------------------------------------
# FRU helpers
# ---------------------------------------------------------------------------

def compare_fru_board(fru: dict, fru_expected: dict) -> None:
    """Compare board area between two parsed FRU dicts."""
    fru_board = fru.get("board")
    assert fru_board, "No Board Area info in FRU data"
    fru_board_expected = fru_expected.get("board")
    assert fru_board_expected, "No Board Area info in FRU expected data"
    for key, value in fru_board_expected.items():
        if key in ("date", "serial"):
            continue
        logger.info(f"Checking FRU board {key}: expected {value}, got {fru_board.get(key)}")
        assert fru_board.get(key) == value, (
            f"FRU data mismatch, expected {value} but got {fru_board.get(key)}"
        )

def dump_fru(bmc_ip: str, fru_id: str) -> Path:
    """Dump FRU data from the BMC."""
    cmd = f"nitro-bmc -i {bmc_ip} fru readraw --fru-id{fru_id} --out-file fru{fru_id}.bin"
    execute(cmd)
    fru_bin = Path(f"fru{fru_id}.bin")
    if not fru_bin.is_file():
        raise RuntimeError(f"Failed to dump FRU data from the BMC")
    return fru_bin

def compare_fru_bin(uut_sn: str, fru_bin: Path, fru_expected: Path) -> None:
    """Compare FRU data between two files."""
    parsed_fru = parse_fru(fru_bin)
    parsed_fru_expected = parse_fru(fru_expected)
    compare_fru_board(parsed_fru, parsed_fru_expected)
    sn_in_fru = parsed_fru["board"]["serial"]["data"]
    logger.info(f"sn in FRU: {sn_in_fru}")
    assert sn_in_fru == uut_sn, (
        f"SN is mismatch, expected: {uut_sn}, actual: {sn_in_fru}"
    )

@contextmanager
def disable_capture(capsys):
    """ Disable pytest's output capture for the duration of the context manager."""
    capmanager = capsys.request.config.pluginmanager.getplugin("capturemanager")
    capmanager.suspend(in_=True)
    try:
        yield
        sys.stdout.flush()
    finally:
        capmanager.resume()


def check_target_status(capsys, target_name, expected_state):
    """Check if the target is in the expected state."""
    logger.info(f"Checking if the {target_name} is {expected_state}")
    prompt = (
        f"Is the {target_name} {expected_state}? Please input y or n: "
    )
    with disable_capture(capsys):
        # Pytest progress uses '\\r' on the same line; start prompt on a fresh line.
        print(flush=True)
        while True:
            ans = input(prompt)
            if ans.lower() in ["y", "n"]:
                break
    logger.info(f"User answer: {ans}")
    assert ans.lower() == "y", f"The {target_name} isn't {expected_state}."

@retry(retries=30, delay=2)
def check_dhcp_ip():
    logger.info('Getting DHCP IP address from JRD')
    p = execute('bash scripts/get_ip.sh')
    assert p.returncode == 0