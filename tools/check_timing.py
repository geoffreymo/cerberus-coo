#!/usr/bin/env python3
"""Check frame timing consistency in FITS cubes.

Usage:
    python tools/check_timing.py [directory_or_file]

If no argument given, checks the latest capture directory.
Flags frames where the interval deviates >10% from the median.
When GPS timestamps are present, checks cam/GPS agreement.
"""

import sys
import numpy as np
from pathlib import Path
from astropy.io import fits


def check_file(fpath):
    fname = Path(fpath).name
    with fits.open(fpath) as hdul:
        table = None
        for h in hdul:
            if h.__class__.__name__ == 'BinTableHDU' and h.data is not None:
                table = h
                break

        if table is None:
            print(f"{fname}: no timestamp table")
            return

        cols = [c.name for c in table.columns]
        if 'TIMESTAMP' not in cols:
            print(f"{fname}: no TIMESTAMP column (cols={cols})")
            return

        ts = np.array(table.data['TIMESTAMP'], dtype=float)
        has_gps = 'GPSTIME' in cols
        gps = np.array(table.data['GPSTIME'], dtype=float) if has_gps else None

        nframes = len(ts)
        if nframes < 2:
            print(f"{fname}: {nframes} frame(s)")
            return

        cam_dt = np.diff(ts)
        med = np.median(cam_dt)
        if med <= 0:
            print(f"{fname}: {nframes}fr, zero median dt")
            return

        # Find timing anomalies (>10% deviation from median)
        bad = np.where(np.abs(cam_dt - med) / med > 0.10)[0]
        max_dev = np.max(np.abs(cam_dt - med)) / med * 100

        # Check cam/GPS consistency
        gps_info_summary = ""
        gps_dt = None
        gps_disagreements = []
        if has_gps:
            valid = ~np.isnan(gps)
            n_valid = np.sum(valid[:-1] & valid[1:])
            if n_valid > 0:
                gps_dt = np.diff(gps)
                both_valid = valid[:-1] & valid[1:]
                if np.any(both_valid):
                    residuals = np.abs(cam_dt[both_valid] - gps_dt[both_valid])
                    max_residual = np.max(residuals)
                    mean_residual = np.mean(residuals)
                    gps_disagreements = np.where(both_valid)[0][residuals > 0.01]
                    gps_info_summary = f", cam-gps: max={max_residual*1e3:.1f}ms mean={mean_residual*1e6:.0f}us"
            n_missing = np.sum(np.isnan(gps))
            if n_missing > 0:
                gps_info_summary += f", {n_missing} missing GPS"

        if len(bad) == 0:
            print(f"{fname}: {nframes}fr, med_dt={med:.6f}s -- CLEAN (max dev {max_dev:.2f}%){gps_info_summary}")
        else:
            print(f"{fname}: {nframes}fr, med_dt={med:.6f}s -- {len(bad)} ANOMALIES (max dev {max_dev:.1f}%){gps_info_summary}")
            for i in bad:
                gps_str = ""
                if gps_dt is not None and not np.isnan(gps[i]) and not np.isnan(gps[i + 1]):
                    gps_str = f", gps_dt={gps_dt[i]:.6f}s, diff={abs(cam_dt[i] - gps_dt[i])*1e3:.1f}ms"
                elif has_gps:
                    gps_str = ", gps=N/A"
                dev = (cam_dt[i] - med) / med * 100
                print(f"  frame {i:3d}->{i+1:3d}: cam_dt={cam_dt[i]:.6f}s ({dev:+.1f}%){gps_str}")

        if len(gps_disagreements) > 0:
            print(f"  WARNING: {len(gps_disagreements)} frames where cam/GPS disagree by >10ms:")
            for i in gps_disagreements[:5]:
                print(f"    frame {i:3d}->{i+1:3d}: cam_dt={cam_dt[i]:.6f}s, gps_dt={gps_dt[i]:.6f}s, diff={abs(cam_dt[i] - gps_dt[i])*1e3:.1f}ms")


def main():
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        base = "/data/cerberus"
        dirs = sorted(Path(base).glob("captures_*"))
        if not dirs:
            print("No capture directories found")
            return
        target = str(dirs[-1])
        subdirs = [d for d in Path(target).iterdir() if d.is_dir()]
        if subdirs:
            target = str(sorted(subdirs)[-1])
        print(f"Checking: {target}\n")

    target = Path(target)
    if target.is_file():
        check_file(str(target))
    elif target.is_dir():
        files = sorted(target.glob("*.fits"))
        if not files:
            print(f"No FITS files in {target}")
            return
        for f in files:
            check_file(str(f))
    else:
        print(f"Not found: {target}")


if __name__ == '__main__':
    main()
