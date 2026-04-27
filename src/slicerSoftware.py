from __future__ import annotations
import os
import json
import re
import math

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

    if config.get("steps_per_mm_z", 0) <= 0:
        errors.append("steps_per_mm_z must be a positive number")

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
steps_per_mm_z = configFile.get("steps_per_mm_z", 400)

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

def extract_traces(gbr_path: str) -> list[tuple[float, float]]:
    """Extract and normalize X/Y trace coordinates from a .gbr file (D01 draw commands)."""
    gerber_file = GerberFile.from_file(gbr_path)
    coords = []
    for match in re.finditer(r'X(-?\d+)Y(-?\d+)D01', gerber_file.source_code):
        x = int(match.group(1)) / 1_000_000
        y = int(match.group(2)) / 1_000_000
        coords.append((x, y))
    if not coords:
        return coords
    min_x = min(c[0] for c in coords)
    min_y = min(c[1] for c in coords)
    return [(x - min_x, y - min_y) for x, y in coords]

def calculate_fill_passes(trace_width_mm: float, nozzle_size_mm: float) -> int:
    """Calculate number of parallel passes needed to fill a trace width."""
    return math.ceil(trace_width_mm / nozzle_size_mm)

def generate_fill_offsets(x: float, y: float, next_x: float, next_y: float, nozzle_size: float, passes: int) -> list[tuple[float, float]]:
    """
    Generate parallel offset positions for multi-pass trace filling.
    Offsets are perpendicular to the trace direction.
    """
    dx = next_x - x
    dy = next_y - y
    length = math.sqrt(dx**2 + dy**2)
    if length == 0:
        return [(x, y)]

    # perpendicular unit vector
    perp_x = -dy / length
    perp_y = dx / length

    points = []
    for i in range(passes):
        offset = (i - (passes - 1) / 2) * nozzle_size
        ox = max(0, x + perp_x * offset)
        oy = max(0, y + perp_y * offset)
        points.append((ox, oy))
    return points

def camera_sweep(g, safe_z: float, board_size_x: float = 0, board_size_y: float = 0, layer_index: int = 0) -> bool:
    """
    Camera sweep after each ink + cure sequence.
    Moves to sweep position and triggers CV system to check for shorts/coverage.
    Returns True if pass, False if fail.
    
    PLACEHOLDER - sweep pattern and CV communication to be confirmed with camera team
    """
    g.rapid(z=safe_z)
    g.rapid(x=0, y=0)

    # PLACEHOLDER - trigger CV system here
    # CV system should receive: board_size_x, board_size_y, layer_index
    # CV system should return: pass/fail
    # Example future implementation:
    # result = cv_system.check(board_size_x, board_size_y, layer_index)
    # if not result:
    #     print(f"  CV check failed on layer {layer_index} — stopping print")
    #     return False

    print(f"camera sweep layer {layer_index} (placeholder) — board {board_size_x}x{board_size_y}mm")
    return True

def deposit_insulator(g, coords: list, work_z: float, safe_z: float, nozzle_size: float) -> None:
    """
    Deposit insulator layer (ACI SI3104) over the entire board surface.
    Uses a raster fill pattern to cover all copper traces.
    Insulator is on a separate head — head offset applied from config.
    Cure: 135C for 5-15 minutes after deposition.
    """
    if not coords:
        print("  no coords for insulator layer, skipping")
        return

    # apply head offset for insulator head
    offset_x = configFile.get("insulator_head_offset_x", 0)
    offset_y = configFile.get("insulator_head_offset_y", 0)

    insulator_cure_seconds = configFile.get("insulator_cure_seconds", 600)

    print(f"  depositing insulator over {len(coords)} points")
    for x, y in coords:
        ox = max(0, x + offset_x)
        oy = max(0, y + offset_y)
        g.rapid(point=(ox, oy))
        g.move(z=work_z)
        g.rapid(z=safe_z)

    # cure insulator at 135C for 5-15 minutes
    print(f"  insulator cure: 135C for {insulator_cure_seconds}s")
    g.set_bed_temperature(configFile.get("insulator_cure_temp", 135))
    g.sleep(insulator_cure_seconds)
    g.set_bed_temperature(0)

# derive output .gcode path from config
gerber_zip_path = configFile.get("gerberFile", "TestFiles/test-gbr.zip")
gerber_dir      = os.path.join(BASE_DIR, "..", os.path.dirname(gerber_zip_path))
gerber_name     = os.path.splitext(os.path.basename(gerber_zip_path))[0]
output_file     = os.path.join(gerber_dir, gerber_name + ".gcode")

# load .gbrjob path from config
gerber_job_path = os.path.join(BASE_DIR, "..", configFile.get("gerberJobFile", "TestFiles/test-job.gbrjob"))
gerber_job      = GerberJobFile.from_file(gerber_job_path)
project         = gerber_job.to_project()

# pull board info from job file
board_size_x    = gerber_job.general_specs.size.x
board_size_y    = gerber_job.general_specs.size.y
board_layers    = gerber_job.general_specs.layer_number
copper_thickness = next(
    (s.thickness for s in gerber_job.material_stackup if s.type == "Copper"),
    0.035
)

print(f"Board: {board_size_x}x{board_size_y}mm, {board_layers} layers, copper thickness: {copper_thickness}mm")

# auto set layer mode based on board layers if not set in config
if "layerMode" not in configFile:
    layer_mode = "multi" if board_layers > 1 else "single"
    print(f"Auto layer mode: {layer_mode}")

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
    layer_mode  = configFile.get("layerMode", "multi")
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
        out_of_bounds = [(x, y) for x, y in coords if x > board_size_x or y > board_size_y]
        if out_of_bounds:
            print(f"WARNING: {len(out_of_bounds)} coords exceed board size ({board_size_x}x{board_size_y}mm) — skipping them")
            coords = [(x, y) for x, y in coords if x <= board_size_x and y <= board_size_y]

        # calculate Z depth for this layer based on layer height from config
        layer_height = configFile.get("layerHeight", 0.2)
        work_z       = -(layer_index * layer_height + layer_height)
        safe_z       = work_z + 5
        print(f"  processing {fa.path} ({fa.file_function}) — {len(coords)} pads — Z depth: {work_z:.2f}mm")

        for x, y in coords:
            g.rapid(point=(x, y))
            g.move(z=work_z)
            g.rapid(z=safe_z)

# process traces with fill passes based on nozzle size
        traces      = extract_traces(gbr_path)
        nozzle_size = configFile.get("nozzleSize", 0.225)
        trace_width = configFile.get("traceWidth", 0.25)
        fill_passes = calculate_fill_passes(trace_width, nozzle_size)

        if traces:
            print(f"  processing {len(traces)} trace points — {fill_passes} fill pass(es) per trace")
            for i in range(len(traces) - 1):
                x,  y  = traces[i]
                nx, ny = traces[i + 1]
                offsets = generate_fill_offsets(x, y, nx, ny, nozzle_size, fill_passes)
                for ox, oy in offsets:
                    g.rapid(point=(ox, oy))
                    g.move(z=work_z)
                    g.rapid(z=safe_z)
        else:
            print(f"  no traces found in {fa.path}")

        # Conductor 3 two-stage cure — dry then sinter
        # Stage 1: dry at 90C for 5 minutes
        print(f"cure stage 1: dry 90C for 5min")
        g.set_bed_temperature(configFile.get("cure_dry_temp", 90))
        g.sleep(cure_dry_seconds)

        # Stage 2: sinter at 170C for 15 minutes
        print(f"cure stage 2: sinter 170C for 15min")
        g.set_bed_temperature(configFile.get("cure_temp", 170))
        g.sleep(cure_seconds)

        # cool down before camera sweep
        g.set_bed_temperature(0)

        # camera sweep after cure
        sweep_passed = camera_sweep(g, safe_z, board_size_x, board_size_y, layer_index)
        if not sweep_passed:
            print(f"  camera sweep failed on layer {layer_index} — stopping print")
            break

        # deposit insulator between copper layers in multi mode
        if layer_mode == "multi" and layer_index < board_layers - 1:
            print(f"  depositing insulator between layers")
            deposit_insulator(g, coords, work_z, safe_z, nozzle_size)

        layer_index += 1

    g.tool_off()
    g.rapid(x=0, y=0)
    g.stop()

print(f"G-code written to {output_file}")
