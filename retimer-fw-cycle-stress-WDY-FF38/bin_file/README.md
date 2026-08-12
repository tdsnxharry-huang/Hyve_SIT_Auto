# bin_file

Place the retimer firmware images used by `retimer_fw_cycle_stress.py` here.
These are proprietary vendor firmware binaries and are **not** committed to
this repository (see `.gitignore`).

Defaults expected by the script (override with `--upgrade-bin` /
`--downgrade-bin` / `--upgrade-version` / `--downgrade-version` if your
filenames or versions differ):

| Role      | Default filename                                          | Version |
|-----------|------------------------------------------------------------|---------|
| Upgrade   | `pt516_x16_normal_hyve__SRNS__2.13_Ryan__v2_13_9.bin`       | 2.13.9  |
| Downgrade | `pt516_x16_normal_SRNS_v2_13_0.bin`                          | 2.13.0  |

Copy your two prepared FW versions into this folder before running the test.
