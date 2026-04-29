"""Convert IQ files between cf32 (numpy complex64) and cs8 (HackRF format).

SENTINEL captures and synthetic-source dumps use cf32.  ``hackrf_transfer``
expects interleaved signed-int8 (cs8).  This CLI handles both directions.

Usage
-----
    python -m tools.hackrf_tx.convert_iq input.cf32 output.cs8
    python -m tools.hackrf_tx.convert_iq --reverse input.cs8 output.cf32
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from tools.hackrf_tx.synth import cf32_to_cs8, cs8_to_cf32


def main() -> None:
    p = argparse.ArgumentParser(
        description="Convert IQ between cf32 (numpy complex64) and cs8 (HackRF interleaved int8)",
    )
    p.add_argument("input", type=Path, help="input file path")
    p.add_argument("output", type=Path, help="output file path")
    p.add_argument(
        "--reverse",
        action="store_true",
        help="convert cs8 → cf32 instead of cf32 → cs8",
    )
    args = p.parse_args()

    if not args.input.exists():
        raise SystemExit(f"input file not found: {args.input}")

    args.output.parent.mkdir(parents=True, exist_ok=True)

    if args.reverse:
        cs8 = np.fromfile(args.input, dtype=np.int8)
        iq = cs8_to_cf32(cs8)
        iq.tofile(args.output)
        print(
            f"  cs8 → cf32:  {args.input}  →  {args.output}  "
            f"({len(iq)} complex samples, {args.output.stat().st_size / 1024:.1f} KiB)"
        )
    else:
        iq = np.fromfile(args.input, dtype=np.complex64)
        cs8 = cf32_to_cs8(iq)
        cs8.tofile(args.output)
        print(
            f"  cf32 → cs8:  {args.input}  →  {args.output}  "
            f"({len(iq)} complex samples, {args.output.stat().st_size / 1024:.1f} KiB)"
        )


if __name__ == "__main__":
    main()
