from __future__ import annotations
import os
import json
import re

from pygerber.gerber.api import GerberFile, GerberJobFile
from gscrib import GCodeBuilder
from GUI import GUI

# ── LAYER ORDER (future implementation) ──────────────────────
# Layer 1   - Conductive ink
# Layer 1.5 - Cure
# Layer 2   - Camera sweep
# --- single layer boards stop here ---
# Layer 3   - Insulator
# Layer 3.5 - Cure
# Layer 4   - Camera sweep (check for shorts)
# --- single side boards stop here ---
# Layer 5   - Conductive ink
# Layer 5.5 - Cure
# Layer 6   - Camera sweep
# Repeat for n layers
#
# Note: insulator cover type determines if stop is at layer 3.5 or 4

# absolute path to the src/ folder so all file paths work regardless of where you run the script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# load machine settings from config.json
with open(os.path.join(BASE_DIR, "config.json"), "r") as f:
    configFile = json.load(f)

# ⚠ PLACEHOLDER - confirm real values from hardware team (machine-specific steps/mm)
steps_per_mm_x = configFile.get("steps_per_mm_x", 80)
steps_per_mm_y = configFile.get("steps_per_mm_y", 80)

# ⚠ PLACEHOLDER - confirm real cure time (seconds) from hardware team
cure_time_seconds = configFile.get("cure_time_seconds", 30)

def coord_to_steps(x_mm: float, y_mm: float) -> tuple[int, int]:
    """Convert Gerber mm coordinates to machine step counts."""
    return round(x_mm * steps_per_mm_x), round(y_mm * steps_per_mm_y)

# load the full Gerber project from the job file (contains all layers)
gerber_job = GerberJobFile.from_file(os.path.join(BASE_DIR, "../TestFiles/test-job.gbrjob"))
project = gerber_job.to_project()

# use files_attributes from the job file — gives us path + official layer function
# file_function values: Copper, SolderPaste, SolderMask, Legend, Profile
def get_layer_type(file_function: str) -> str:
    f = file_function.lower()
    if "copper" in f:       return "copper"
    if "solderpaste" in f:  return "paste"
    if "soldermask" in f:   return "mask"
    if "legend" in f:       return "silkscreen"
    if "profile" in f:      return "edge"
    return "unknown"

def extract_coords(gbr_path: str) -> list[tuple[float, float]]:
    """Extract and normalize X/Y pad coordinates from a .gbr file."""
    gerber_file = GerberFile.from_file(gbr_path)
    coords = []
    for match in re.finditer(r'X(-?\d+)Y(-?\d+)D03', gerber_file.source_code):
        x = int(match.group(1)) / 1_000_000
        y = int(match.group(2)) / 1_000_000
        coords.append((x, y))
    if not coords:
        return coords
    min_x = min(c[0] for c in coords)
    min_y = min(c[1] for c in coords)
    return [(x - min_x, y - min_y) for x, y in coords]

# derive output .gcode path from config
gerber_zip_path = configFile.get("gerberFile", "TestFiles/test-gbr.zip")
gerber_dir      = os.path.join(BASE_DIR, "..", os.path.dirname(gerber_zip_path))
gerber_name     = os.path.splitext(os.path.basename(gerber_zip_path))[0]
output_file     = os.path.join(gerber_dir, gerber_name + ".gcode")

# write G-code using gscrib
with GCodeBuilder(output=output_file) as g:
    g.set_bounds("axes", min=(0, 0, -50), max=(configFile["maxBedSize"][0], configFile["maxBedSize"][1], 50))
    g.set_axis(point=(0, 0, 0))
    g.set_length_units("millimeters")
    g.set_time_units("seconds")
    g.set_distance_mode("absolute")
    g.set_feed_rate(configFile.get("printSpeed", 60) * 10)

    g.rapid(z=5)
    g.tool_on("clockwise", 1000)
    g.sleep(1)

    for fa in gerber_job.files_attributes:
        layer_type = get_layer_type(fa.file_function)

        # only process copper layers for now
        if layer_type != "copper":
            print(f"  skipping {fa.path} ({layer_type})")
            continue

        gbr_path = os.path.join(BASE_DIR, "../TestFiles", fa.path)
        coords   = extract_coords(gbr_path)

        if not coords:
            print(f"  no pads found in {fa.path}, skipping")
            continue

        print(f"  processing {fa.path} ({fa.file_function}) — {len(coords)} pads")

        for x, y in coords:
            g.rapid(point=(x, y))
            g.move(z=-2)
            g.rapid(z=5)

        # ⚠ PLACEHOLDER - cure dwell after each copper layer (confirm cure_time_seconds with hardware team)
        g.sleep(cure_time_seconds)

    g.tool_off()
    g.rapid(x=0, y=0)
    g.stop()

print(f"G-code written to {output_file}")
