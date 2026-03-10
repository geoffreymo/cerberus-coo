#!/usr/bin/env python3
"""Quick script to show HDU2 (TIMESTAMPS) from latest FITS file."""

import sys
from pathlib import Path
from astropy.io import fits
from astropy.table import Table

# Get directory from arg or use default
directory = sys.argv[1] if len(sys.argv) > 1 else "/data/cerberus/captures_2026_03_06/PHX4"

# Find latest .fits file
fits_files = sorted(Path(directory).glob("*.fits"), key=lambda p: p.stat().st_mtime, reverse=True)

if not fits_files:
    print(f"No FITS files found in {directory}")
    sys.exit(1)

latest = fits_files[0]
print(f"File: {latest.name}")
print()

with fits.open(latest) as hdul:
    print(f"HDUs: {[h.name for h in hdul]}")
    print()

    # Show primary header GPS fields
    print("=== Primary Header (GPS fields) ===")
    for key in ['GPSSTART', 'DATE-OBS', 'OBJECT', 'EXPTIME', 'NFRAMES']:
        if key in hdul[0].header:
            print(f"  {key}: {hdul[0].header[key]}")
    print()

    # Show HDU2 if it exists
    if len(hdul) > 2:
        print(f"=== HDU 2: {hdul[2].name} ===")
        print(f"Columns: {hdul[2].columns.names}")
        print()

        tbl = Table.read(hdul[2])
        print(f"Rows: {len(tbl)}")
        print()
        print("First 10 rows:")
        print(tbl[:10])
        print()
        print("Last 5 rows:")
        print(tbl[-5:])
    else:
        print("No HDU 2 found")
