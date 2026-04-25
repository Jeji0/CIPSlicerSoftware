from __future__ import annotations
import os
import json
import re

from pygerber.gerber.api import GerberFile, GerberJobFile
from gscrib import GCodeBuilder
from GUI import GUI

# ── LAYER ORDER (future implementation) ──────────────────────
# Layer 1   - Conductive ink (Conductor 3, face up)
# Layer 1.5 - Cure stage 1: dry 90°C for 5min
# Layer 1.75 - Cure stage 2: sinter 170°C for 15min
# Layer 2   - Camera sweep
# --- single layer boards stop here ---
# Layer 3   - Insulator
# Layer 3.5 - Cure
# Layer 4   - Camera sweep (check for shorts)
# --- single side boards stop here ---
# Layer 5   - Conductive ink
# Layer 5.5 - Cure stage 1: dry 90°C for 5min
# Layer 5.75 - Cure stage 2: sinter 170°C for 15min
# Layer 6   - Camera sweep
# Repeat for n layers
#
# Note: Conductor 3 — no burnishing needed, no flipping needed
# Note: insulator cover type determines if stop is at layer 3.5 or 4

# absolute path to the src/ folder so all file paths work regardless of where you run the script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# load machine settings from config.json
with open(os.path.join(BASE_DIR, "config.json"), "r") as f:
    configFile = json.load(f)

def validate_config(config: dict) -> None:
    """Validate config.json values before running — catch bad inputs early."""
    errors = []

    bed = config.get("maxBedSize")
    if not bed or len(bed) != 3 or any(v <= 0 for v in bed):
        errors.append("maxBedSize must be a list of 3 positive numbers [x, y, z]")

    if config.get("printSpeed", 0) <= 0:
        errors.append("printSpeed must be a positive number")

    if config.get("steps_per_mm_x", 0) <= 0:
        errors.append("steps_per_mm_x must be a positive number")

    if config.get("steps_per_mm_y", 0) <= 0:
        errors.append("steps_per_mm_y must be a positive number")

    if config.get("cure_dry_seconds", 0) < 0:
        errors.append("cure_dry_seconds cannot be negative")
    if config.get("cure_seconds", 0) < 0:
        errors.append("cure_seconds cannot be negative")

    if not config.get("gerberFile", ""):
        errors.append("gerberFile path is missing from config")

    if config.get("layerMode", "single") not in ["single", "multi"]:
        errors.append("layerMode must be 'single' or 'multi'")

    if errors:
        print("Config validation failed:")
        for e in errors:
            print(f"  ✗ {e}")
        raise SystemExit(1)

    print("Config validation passed")

validate_config(configFile)

#  PLACEHOLDER - confirm real values (machine-specific steps/mm)
steps_per_mm_x = configFile.get("steps_per_mm_x", 80)
steps_per_mm_y = configFile.get("steps_per_mm_y", 80)

# Conductor 3 two-stage cure process
# Stage 1: dry at 90°C for 5 minutes
# Stage 2: sinter at 170°C for 15 minutes
cure_dry_seconds = configFile.get("cure_dry_seconds", 300)
cure_seconds     = configFile.get("cure_seconds", 900)

def coord_to_steps(x_mm: float, y_mm: float) -> tuple[int, int]:
    """Convert Gerber mm coordinates to machine step counts."""
    return round(x_mm * steps_per_mm_x), round(y_mm * steps_per_mm_y)

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

def camera_sweep(g, safe_z: float) -> None:
    """
    PLACEHOLDER - camera sweep after each ink + cure sequence
    Moves the camera across the board to check for shorts / coverage
    Exact pattern and coordinates to be confirmed with camera team
    Currently just moves to origin and back as a stub
    """
    #PLACEHOLDER - replace with real sweep pattern from camera team
    g.rapid(z=safe_z)
    g.rapid(x=0, y=0)
    print("camera sweep (placeholder)")

# derive output .gcode path from config
gerber_zip_path = configFile.get("gerberFile", "TestFiles/test-gbr.zip")
gerber_dir      = os.path.join(BASE_DIR, "..", os.path.dirname(gerber_zip_path))
gerber_name     = os.path.splitext(os.path.basename(gerber_zip_path))[0]
output_file     = os.path.join(gerber_dir, gerber_name + ".gcode")

# load .gbrjob path from config
gerber_job_path = os.path.join(BASE_DIR, "..", configFile.get("gerberJobFile", "TestFiles/test-job.gbrjob"))
gerber_job      = GerberJobFile.from_file(gerber_job_path)
project         = gerber_job.to_project()

# auto unzip gerber file if not already extracted
import zipfile

gerber_zip_full = os.path.join(BASE_DIR, "..", gerber_zip_path)
extract_dir     = os.path.join(BASE_DIR, "../TestFiles")

if gerber_zip_path.endswith(".zip") and os.path.exists(gerber_zip_full):
    with zipfile.ZipFile(gerber_zip_full, "r") as z:
        z.extractall(extract_dir)
    print(f"Extracted {gerber_zip_path} to {extract_dir}")
else:
    print(f"Skipping extraction — {gerber_zip_path} already extracted or not a zip")

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

    # single mode = top copper only, multi mode = all copper layers
    layer_mode  = configFile.get("layerMode", "single")
    layer_index = 0
    print(f"Layer mode: {layer_mode}")

    for fa in gerber_job.files_attributes:
        layer_type = get_layer_type(fa.file_function)

        # only process copper layers for now
        if layer_type != "copper":
            print(f"  skipping {fa.path} ({layer_type})")
            continue

        # in single mode skip bottom copper layers
        if layer_mode == "single" and "bot" in fa.file_function.lower():
            print(f"  skipping {fa.path} (single layer mode)")
            continue

        gbr_path = os.path.join(BASE_DIR, "../TestFiles", fa.path)
        coords   = extract_coords(gbr_path)

        if not coords:
            print(f"  no pads found in {fa.path}, skipping")
            continue

        # check if any coords exceed bed size and skip them
        max_x = configFile["maxBedSize"][0]
        max_y = configFile["maxBedSize"][1]
        out_of_bounds = [(x, y) for x, y in coords if x > max_x or y > max_y]
        if out_of_bounds:
            print(f"WARNING: {len(out_of_bounds)} coords exceed bed size ({max_x}x{max_y}mm) — skipping them")
            coords = [(x, y) for x, y in coords if x <= max_x and y <= max_y]

        # calculate Z depth for this layer based on layer height from config
        layer_height = configFile.get("layerHeight", 0.2)
        work_z       = -(layer_index * layer_height + layer_height)
        safe_z       = work_z + 5
        print(f"  processing {fa.path} ({fa.file_function}) — {len(coords)} pads — Z depth: {work_z:.2f}mm")

        for x, y in coords:
            g.rapid(point=(x, y))
            g.move(z=work_z)
            g.rapid(z=safe_z)

        # Conductor 3 two-stage cure — dry then sinter
        # temperature control depends on hardware heater setup
        print(f"cure stage 1: dry 90°C for 5min")
        g.sleep(cure_dry_seconds)
        print(f"cure stage 2: sinter 170°C for 15min")
        g.sleep(cure_seconds)

        # camera sweep after cure
        camera_sweep(g, safe_z)

        layer_index += 1

    g.tool_off()
    g.rapid(x=0, y=0)
    g.stop()

print(f"G-code written to {output_file}")
