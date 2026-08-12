"""Wrappers for tools/frugen (fru_gen_nitro.py + nitro-bmc CLI).

Typical OOB flow for boards without pre-programmed FRU (e.g. RetimerBD):
1. Export CSV from a template bin, or read FRU from the BMC and export CSV.
2. Manually edit serial number, board mfg date, and other fields in the CSV.
3. Generate a binary from the CSV, write it to the BMC, and reboot the BMC if needed.
"""
from __future__ import annotations

import csv
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from libs.helpers import execute

# English abbreviations so board_mfg_date matches fru_gen_nitro string_to_board_time (%a %b ...)
# regardless of system locale (see tools/frugen/nitro_sample.csv).
_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_ABBR = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

# Repository root (assumes this file is at libs/frugen.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
FRUGEN_DIR = _REPO_ROOT / "tools" / "frugen"
FRU_GEN_NITRO_PY = FRUGEN_DIR / "fru_gen_nitro.py"
NITRO_BMC_CLI = FRUGEN_DIR / "nitro-bmc-cli" / "nitro-bmc"


def frugen_root() -> Path:
    """Return the tools/frugen directory (required as cwd when running fru_gen_nitro)."""
    return FRUGEN_DIR


def format_nitro_board_mfg_date(dt: datetime | None = None) -> str:
    """
    Format a timestamp for the Nitro CSV ``board_mfg_date`` field.

    Uses the same pattern as ``nitro_sample.csv`` and ``fru_gen_nitro.string_to_board_time``:
    ``%a %b %d %H:%M:%S %Y`` (UTC). Naive ``dt`` is treated as UTC.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    wday = _WEEKDAY_ABBR[dt.weekday()]
    mon = _MONTH_ABBR[dt.month - 1]
    return (
        f"{wday} {mon} {dt.day:02d} "
        f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d} {dt.year}"
    )


def update_nitro_fru_csv(
    csv_path: str | Path,
    *,
    board_serial_number: str,
    board_mfg_date: str | None = None,
    mfg_at: datetime | None = None,
) -> Path:
    """
    Update ``board_serial_number`` and ``board_mfg_date`` in a Nitro FRU CSV (``nitro_sample.csv`` style).

    Rows are key,value pairs readable by ``fru_gen_nitro.load_csv`` (values may contain commas).

    - ``board_mfg_date``: if omitted, set from ``mfg_at`` or current UTC time via
      :func:`format_nitro_board_mfg_date`.
    - If ``board_mfg_date`` is passed explicitly, it is written as-is (must match
      ``fru_gen_nitro`` expectations, e.g. ``Mon Jan 10 17:46:00 2022``).

    Returns the resolved path to the written file.
    """
    path = Path(csv_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    if board_mfg_date is None:
        board_mfg_date = format_nitro_board_mfg_date(mfg_at)

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows: list[list[str]] = []
        for row in reader:
            if not row:
                continue
            key = row[0]
            value_parts = row[1:] if len(row) > 1 else []
            value = ""
            for i, part in enumerate(value_parts):
                if i:
                    value += ","
                value += part
            rows.append([key, value])

    key_to_index = {r[0]: i for i, r in enumerate(rows)}
    for field in ("board_serial_number", "board_mfg_date"):
        if field not in key_to_index:
            raise KeyError(f"CSV missing required field {field!r}: {path}")

    rows[key_to_index["board_serial_number"]][1] = board_serial_number
    rows[key_to_index["board_mfg_date"]][1] = board_mfg_date

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, lineterminator="\n")
        for key, value in rows:
            writer.writerow([key, value])

    return path


def _resolve_under_frugen(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    workdir = cwd if cwd is not None else FRUGEN_DIR
    return execute(argv, cwd=workdir, check=check)


def nitro_fru_revert_org_bin_to_csv(
    fru_id: str,
    bmc_ip: str,
    org_fru_bin: str | Path,
    csv_out: str | Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Load fields from an original FRU binary (e.g. FRU_BIN_ORG_RetimerBD) and write a CSV.

    Equivalent to:
    python3 fru_gen_nitro.py -id <id> -ip <bmc> -i <org.bin> -re <out.csv>

    Note: with -i, the script fills bin_dict from that file and does not read FRU from the
    BMC. To collect the on-board serial from the BMC, use nitro_fru_read_bmc_to_csv instead.
    """
    argv = [
        "python3",
        str(FRU_GEN_NITRO_PY),
        "-id",
        str(fru_id),
        "-ip",
        bmc_ip,
        "-i",
        _resolve_under_frugen(org_fru_bin),
        "-re",
        _resolve_under_frugen(csv_out),
    ]
    return _run(argv, check=check)


def nitro_fru_read_bmc_to_csv(
    fru_id: str,
    bmc_ip: str,
    csv_out: str | Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Read FRU from the BMC and export it as CSV (if bin_dict is empty, the script
    issues fru_read first, then writes the CSV).

    Equivalent to:
    python3 fru_gen_nitro.py -id <id> -ip <bmc> -re <out.csv>
    """
    argv = [
        "python3",
        str(FRU_GEN_NITRO_PY),
        "-id",
        str(fru_id),
        "-ip",
        bmc_ip,
        "-re",
        _resolve_under_frugen(csv_out),
    ]
    return _run(argv, check=check)


def nitro_fru_generate_and_write(
    fru_id: str,
    bmc_ip: str,
    csv_path: str | Path,
    fru_bin: str | Path,
    *,
    fru_len: int = 4096,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Build a FRU binary from an edited CSV and write it to the BMC.

    Equivalent to:
    python3 fru_gen_nitro.py -id <id> -ip <bmc> -csv <csv> -g <bin> -len <n> -w <bin>
    """
    bin_resolved = _resolve_under_frugen(fru_bin)
    argv = [
        "python3",
        str(FRU_GEN_NITRO_PY),
        "-id",
        str(fru_id),
        "-ip",
        bmc_ip,
        "-csv",
        _resolve_under_frugen(csv_path),
        "-g",
        bin_resolved,
        "-len",
        str(fru_len),
        "-w",
        bin_resolved,
    ]
    return _run(argv, check=check)


def write_fru(
    fru_id: str,
    bmc_ip: str,
    org_fru_bin: str | Path,
    uut_sn: str,
    *,
    fru_len: int = 4096,
    check: bool = True,
) -> None:
    """
    One-shot Nitro FRU OOB flow:

    1. Export CSV from the template ``org_fru_bin``.
    2. Patch ``board_serial_number`` / ``board_mfg_date`` for ``uut_sn``.
    3. Generate binary and write it to the BMC.

    Artifacts are stored under ``log_folder`` as ``{uut_sn}_fru.csv`` and ``{uut_sn}.bin``.
    """
    csv_path = Path(f"{uut_sn}_fru.csv")
    out_bin = Path(f"{uut_sn}.bin")

    nitro_fru_revert_org_bin_to_csv(
        fru_id, bmc_ip, org_fru_bin, csv_path, check=check
    )
    update_nitro_fru_csv(csv_path, board_serial_number=uut_sn)
    nitro_fru_generate_and_write(
        fru_id, bmc_ip, csv_path, out_bin, fru_len=fru_len, check=check
    )
