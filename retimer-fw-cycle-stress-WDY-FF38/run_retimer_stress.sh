#!/bin/bash
set -euo pipefail

# Retimer FW upgrade/downgrade endurance loop (100x by default), with an
# AC-cycle checkpoint every 10 cycles. See retimer_fw_cycle_stress.py --help
# for all options, e.g.:
#   ./run_retimer_stress.sh --bmc-ip 10.0.0.5
#   ./run_retimer_stress.sh --bmc-ip 10.0.0.5 --cards retimer1 --cycles 20

detect_pkg_manager() {
	if command -v apt-get >/dev/null 2>&1; then
		echo "apt-get"
		return 0
	fi
	if command -v dnf >/dev/null 2>&1; then
		echo "dnf"
		return 0
	fi
	if command -v yum >/dev/null 2>&1; then
		echo "yum"
		return 0
	fi
	echo ""
}

run_as_root() {
	if [ "$(id -u)" -eq 0 ]; then
		"$@"
		return $?
	fi
	if command -v sudo >/dev/null 2>&1; then
		sudo "$@"
		return $?
	fi
	return 1
}

install_uv() {
	if command -v uv >/dev/null 2>&1; then
		return 0
	fi

	echo "'uv' not found. Trying auto-install ..." >&2
	pm="$(detect_pkg_manager)"

	if [ -z "$pm" ]; then
		echo "Error: unsupported package manager (need apt-get/dnf/yum)." >&2
		echo "Please install uv manually: https://docs.astral.sh/uv/getting-started/installation/" >&2
		exit 1
	fi

	if ! command -v curl >/dev/null 2>&1; then
		echo "Installing curl via $pm ..." >&2
		case "$pm" in
			apt-get)
				run_as_root apt-get update
				run_as_root apt-get install -y curl ca-certificates
				;;
			dnf)
				run_as_root dnf install -y curl ca-certificates
				;;
			yum)
				run_as_root yum install -y curl ca-certificates
				;;
			*)
				echo "Error: unsupported package manager: $pm" >&2
				exit 1
				;;
		esac
	fi

	echo "Installing uv (official installer) ..." >&2
	curl -LsSf https://astral.sh/uv/install.sh | sh

	export PATH="$HOME/.local/bin:$PATH"
	if command -v uv >/dev/null 2>&1; then
		echo "uv installed successfully: $(uv --version)" >&2
		return 0
	fi

	echo "Error: uv installation failed." >&2
	echo "Please install manually: https://docs.astral.sh/uv/getting-started/installation/" >&2
	exit 1
}

install_uv

if [ ! -f "retimer_fw_cycle_stress.py" ]; then
	echo "Error: retimer_fw_cycle_stress.py not found in current directory." >&2
	echo "Please run this script from the project root folder." >&2
	exit 1
fi

uv run --no-sync -- python retimer_fw_cycle_stress.py "$@"
rc="$?"
if [ "$rc" -ne 0 ]; then
	echo "Hint: if Python dependencies are missing, run: uv sync" >&2
fi
exit "$rc"
