from __future__ import annotations
import os
import json
import re
import math
import zipfile

from pygerber.gerber.api import GerberFile, GerberJobFile
from gscrib import GCodeBuilder

from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import unary_union

GERBER_EXTENSIONS = {".gbr", ".gtl", ".gbl", ".gts", ".gbs", ".gto",
                     ".gbo", ".gtp", ".gbp", ".gko", ".ger"}

# absolute path to the src/ folder so all file paths work regardless of where you run the script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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

def generate_shapely_toolpaths(raw_segments, nozzle_size, stepover_ratio=0.85):
    """
    Merges overlapping trace segments and generates concentric fill paths.
    raw_segments: list of ((start_x, start_y), (end_x, end_y), trace_width)
    Returns a list of continuous paths (each path is a list of (x,y) tuples).
    """
    if not raw_segments:
        return []

    # 1. Convert centerlines into actual 2D area polygons
    trace_polys = []
    for (sx, sy), (ex, ey), width in raw_segments:
        line = LineString([(sx, sy), (ex, ey)])
        # Buffer by half the trace width to create the full trace area.
        # cap_style=1 (round) mimics the natural spread of ink at segment ends.
        poly = line.buffer(width / 2.0, cap_style=1, join_style=1)
        trace_polys.append(poly)

    # 2. Boolean Union: Merge all intersecting traces into one continuous geometry
    merged_layer = unary_union(trace_polys)

    # 3. Generate concentric toolpaths (Insetting)
    toolpaths = []
    
    # The first pass must be inset by half the nozzle size so the *edge* # of the extruded ink aligns with the intended trace boundary.
    current_inset = nozzle_size / 2.0 
    stepover_dist = nozzle_size * stepover_ratio

    while True:
        # A negative buffer shrinks the polygon inward
        path_geo = merged_layer.buffer(-current_inset)
        
        if path_geo.is_empty:
            break # We have completely filled the interior of the traces

        # Shapely might return a Polygon or a MultiPolygon (if the inset splits into islands)
        geoms = path_geo.geoms if hasattr(path_geo, 'geoms') else [path_geo]
        
        for geom in geoms:
            if isinstance(geom, Polygon):
                # Add the outer boundary of this shape as a toolpath
                toolpaths.append(list(geom.exterior.coords))
                # Add any inner boundaries (like the edges of vias/holes)
                for interior in geom.interiors:
                    toolpaths.append(list(interior.coords))

        # Move inward for the next concentric fill pass
        current_inset += stepover_dist

    return toolpaths

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

def get_head(configFile: dict, head_type: str) -> dict:
    """Get the first active head of the given type from config.
    Falls back to flat config values if no heads defined."""
    heads = configFile.get("heads", [])
    active = configFile.get("activeHeads", [])
    for head in heads:
        if head.get("id") in active and head.get("type") == head_type:
            return head
    # fallback to flat config for backwards compatibility
    if head_type == "conductive":
        return {
            "nozzleSize": configFile.get("nozzleSize", 0.225),
            "traceWidth": configFile.get("traceWidth", 0.225),
            "cureDryTemp": configFile.get("cure_dry_temp", 90),
            "cureDrySeconds": configFile.get("cure_dry_seconds", 300),
            "cureTemp": configFile.get("cure_temp", 170),
            "cureSeconds": configFile.get("cure_seconds", 900)
        }
    if head_type == "insulator":
        return {
            "nozzleSize": configFile.get("nozzleSize", 0.225),
            "cureTemp": configFile.get("insulator_cure_temp", 135),
            "cureSeconds": configFile.get("insulator_cure_seconds", 600),
            "offsetX": configFile.get("insulator_head_offset_x", 0),
            "offsetY": configFile.get("insulator_head_offset_y", 0)
        }
    return {}

def get_head_for_layer(configFile: dict, layer_type: str) -> dict:
    """Automatically select the correct head based on layer type."""
    if layer_type == "copper":
        return get_head(configFile, "conductive")
    if layer_type == "insulator":
        return get_head(configFile, "insulator")
    return get_head(configFile, "conductive")  # default

def get_tool_number(head: dict) -> int:
    """Get the tool number for a head profile. Defaults to 0."""
    return head.get("toolNumber", 0)

def get_layer_type(file_function: str) -> str:
    """Detect layer type from the Gerber file_function field (KiCad job file)."""
    f = file_function.lower()
    if "copper" in f:       return "copper"
    if "soldermask" in f:   return "mask"
    if "legend" in f:       return "silkscreen"
    if "profile" in f:      return "edge"
    return "unknown"

def get_layer_type_from_filename(filename: str) -> str:
    """Detect layer type from Gerber filename for boards without a standard .gbrjob."""
    f = filename.lower()
    if any(x in f for x in ["copper_top", "f_cu", "top_copper", "gtl"]):
        return "copper_top"
    if any(x in f for x in ["copper_bottom", "b_cu", "bottom_copper", "gbl"]):
        return "copper_bottom"
    if any(x in f for x in ["soldermask_top", "f_mask", "top_mask", "gts"]):
        return "mask_top"
    if any(x in f for x in ["soldermask_bottom", "b_mask", "bottom_mask", "gbs"]):
        return "mask_bottom"
    if any(x in f for x in ["silkscreen_top", "f_silkscreen", "top_silk", "gto"]):
        return "silkscreen_top"
    if any(x in f for x in ["silkscreen_bottom", "b_silkscreen", "bottom_silk", "gbo"]):
        return "silkscreen_bottom"
    if any(x in f for x in ["profile", "edge_cuts", "outline", "gko"]):
        return "edge"
    return "unknown"


def extract_coords(gbr_path: str, offset_x: float = None, offset_y: float = None):
    """Extract X/Y pad coordinates with aperture sizes."""
    gerber_file = GerberFile.from_file(gbr_path)
    source = gerber_file.source_code
    scale = 25.4 if "%MOIN*%" in source else 1.0
    fmt_match = re.search(r'%FSLA[XY](\d)(\d)', source)
    if fmt_match:
        decimal_places = int(fmt_match.group(2))
        divisor = 10 ** decimal_places
    else:
        divisor = 1_000_000

    aperture_sizes  = {}
    aperture_shapes = {}
    for match in re.finditer(r'%ADD(\d+)([A-Za-z]+),([^*]+)\*%', source):
        apt_id   = match.group(1)
        apt_type = match.group(2)
        params   = match.group(3).split('X')
        
        if apt_type == 'C':
            size  = float(params[0]) * scale
            shape = 'C'
        elif apt_type == 'R':
            width  = float(params[0]) * scale
            height = float(params[1]) * scale if len(params) > 1 else width
            size   = width
            shape  = f'RR:{height}'
        elif apt_type == 'RoundRect':
            try:
                dxs    = [abs(float(params[i])) for i in range(1, len(params), 2)]
                dys    = [abs(float(params[i])) for i in range(2, len(params), 2)]
                width  = max(dxs) * 2 if dxs else 0.2
                height = max(dys) * 2 if dys else 0.2
                size  = width
                shape = f'RR:{height}'  
            except:
                size  = 0.6
                shape = 'C'
        else:
            size  = float(params[0]) if params else 0.2
            shape = 'C'
        
        aperture_sizes[apt_id]  = size
        aperture_shapes[apt_id] = shape

    for match in re.finditer(r'G04:AMPARAMS\|DCode=(\d+)\|XSize=([\d.]+)mm\|YSize=([\d.]+)mm[^*]*Shape=(\w+)', source):
        apt_id = match.group(1)
        width  = float(match.group(2))
        height = float(match.group(3))
        shape  = match.group(4)
        if apt_id not in aperture_sizes:
            aperture_sizes[apt_id]  = width
            aperture_shapes[apt_id] = f'RR:{height}' if shape == 'RoundedRectangle' else 'C'

    for match in re.finditer(r'%ADD(\d+)([A-Za-z]\w+)\*%', source):
        apt_id = match.group(1)
        if apt_id not in aperture_sizes:
            aperture_sizes[apt_id]  = 1.4
            aperture_shapes[apt_id] = 'C'

    raw = []
    current_aperture = None
    current_x = 0.0
    current_y = 0.0
    
    for line in source.split('\n'):
        line = line.strip()
        apt_match = re.match(r'D(\d+)\*', line)
        if apt_match and int(apt_match.group(1)) >= 10:
            current_aperture = apt_match.group(1)

        coord_move = re.match(r'X(-?\d+)Y(-?\d+)D02', line)
        if coord_move:
            current_x = int(coord_move.group(1)) / divisor * scale
            current_y = int(coord_move.group(2)) / divisor * scale

        x_only = re.match(r'X(-?\d+)D0[23]\*', line)
        if x_only:
            current_x = int(x_only.group(1)) / divisor * scale

        y_only = re.match(r'Y(-?\d+)D0[23]\*', line)
        if y_only:
            current_y = int(y_only.group(1)) / divisor * scale

        d03_match = re.match(r'X(-?\d+)Y(-?\d+)D03', line)
        if d03_match:
            x = int(d03_match.group(1)) / divisor * scale
            y = int(d03_match.group(2)) / divisor * scale
            size  = aperture_sizes.get(current_aperture, 0.2)
            shape = aperture_shapes.get(current_aperture, 'C')
            raw.append((x, y, size, shape))
        elif re.match(r'D03\*', line):
            size  = aperture_sizes.get(current_aperture, 0.2)
            shape = aperture_shapes.get(current_aperture, 'C')
            raw.append((current_x, current_y, size, shape))

    if not raw:
        return [], 0, 0

    min_x = offset_x if offset_x is not None else min(c[0] for c in raw)
    min_y = offset_y if offset_y is not None else min(c[1] for c in raw)
    return [(x - min_x, y - min_y, size, shape) for x, y, size, shape in raw], min_x, min_y

def approximate_arc(x1, y1, x2, y2, i, j, clockwise, segments=16):
    """Approximate a Gerber arc as linear segments."""
    cx = x1 + i
    cy = y1 + j
    r = math.sqrt(i**2 + j**2)
    
    start_angle = math.atan2(y1 - cy, x1 - cx)
    end_angle   = math.atan2(y2 - cy, x2 - cx)
    
    if clockwise:
        if end_angle >= start_angle:
            end_angle -= 2 * math.pi
    else:
        if end_angle <= start_angle:
            end_angle += 2 * math.pi
    
    points = []
    for n in range(segments + 1):
        t = n / segments
        angle = start_angle + t * (end_angle - start_angle)
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points

def extract_traces(gbr_path: str, offset_x: float = None, offset_y: float = None, min_trace_width: float = 0.225):
    gerber_file = GerberFile.from_file(gbr_path)
    source = gerber_file.source_code
    scale = 25.4 if "%MOIN*%" in source else 1.0
    fmt_match = re.search(r'%FSLA[XY](\d)(\d)', source)
    if fmt_match:
        decimal_places = int(fmt_match.group(2))
        divisor = 10 ** decimal_places
    else:
        divisor = 1_000_000

    aperture_sizes = {}
    for match in re.finditer(r'%ADD(\d+)([A-Za-z]+),([^*]+)\*%', source):
        apt_id   = match.group(1)
        apt_type = match.group(2)
        params   = match.group(3).split('X')
        if apt_type in ('C', 'R'):
            aperture_sizes[apt_id] = float(params[0]) * scale
        else:
            aperture_sizes[apt_id] = float(params[0]) * scale if params else 0.225

    raw_segments = []
    current_x = 0.0
    current_y = 0.0
    prev_x = 0.0
    prev_y = 0.0
    current_aperture = None
    arc_mode = None 

    for line in source.split('\n'):
        line = line.strip()
        
        if 'G02' in line:
            arc_mode = 'G02'
        elif 'G03' in line:
            arc_mode = 'G03'
        elif 'G01' in line:
            arc_mode = None

        apt_match = re.match(r'D(\d+)\*', line)
        if apt_match and int(apt_match.group(1)) >= 10:
            current_aperture = apt_match.group(1)

        trace_width = max(aperture_sizes.get(current_aperture, min_trace_width), min_trace_width)

        arc_match = re.match(r'(?:G0[23])?X(-?\d+)Y(-?\d+)I(-?\d+)J(-?\d+)D01', line)
        if not arc_match:
            arc_match = re.match(r'X(-?\d+)Y(-?\d+)I(-?\d+)J(-?\d+)D01', line)

        if arc_match:
            x  = int(arc_match.group(1)) / divisor * scale
            y  = int(arc_match.group(2)) / divisor * scale
            i  = int(arc_match.group(3)) / divisor * scale
            j  = int(arc_match.group(4)) / divisor * scale
            pts = approximate_arc(current_x, current_y, x, y, i, j, clockwise=(arc_mode == 'G02'))
            for n in range(len(pts) - 1):
                raw_segments.append((pts[n], pts[n+1], trace_width))
            current_x = x
            current_y = y
            continue

        arc_i_match = re.match(r'X(-?\d+)Y(-?\d+)I(-?\d+)D01', line)
        if arc_i_match:
            x = int(arc_i_match.group(1)) / divisor * scale
            y = int(arc_i_match.group(2)) / divisor * scale
            i = int(arc_i_match.group(3)) / divisor * scale
            pts = approximate_arc(current_x, current_y, x, y, i, 0, clockwise=(arc_mode == 'G02'))
            for n in range(len(pts) - 1):
                raw_segments.append((pts[n], pts[n+1], trace_width))
            current_x = x
            current_y = y
            continue

        arc_j_match = re.match(r'X(-?\d+)Y(-?\d+)J(-?\d+)D01', line)
        if arc_j_match:
            x = int(arc_j_match.group(1)) / divisor * scale
            y = int(arc_j_match.group(2)) / divisor * scale
            j = int(arc_j_match.group(3)) / divisor * scale
            pts = approximate_arc(current_x, current_y, x, y, 0, j, clockwise=(arc_mode == 'G02'))
            for n in range(len(pts) - 1):
                raw_segments.append((pts[n], pts[n+1], trace_width))
            current_x = x
            current_y = y
            continue

        x_match = re.match(r'X(-?\d+)', line)
        y_match = re.match(r'.*Y(-?\d+)', line)
        d_match = re.search(r'(D0[123])\*', line)

        if d_match and not arc_match and not arc_i_match and not arc_j_match:
            if x_match:
                current_x = int(x_match.group(1)) / divisor * scale
            if y_match:
                current_y = int(y_match.group(1)) / divisor * scale
            cmd = d_match.group(1)
            if cmd == 'D01':
                raw_segments.append(((prev_x, prev_y), (current_x, current_y), trace_width))
            prev_x = current_x
            prev_y = current_y

    if not raw_segments:
        return raw_segments, 0, 0

    all_x = [s[0][0] for s in raw_segments] + [s[1][0] for s in raw_segments]
    all_y = [s[0][1] for s in raw_segments] + [s[1][1] for s in raw_segments]
    min_x = offset_x if offset_x is not None else min(all_x)
    min_y = offset_y if offset_y is not None else min(all_y)

    normalized = [((sx - min_x, sy - min_y), (ex - min_x, ey - min_y), tw)
                  for (sx, sy), (ex, ey), tw in raw_segments]
    return normalized, min_x, min_y

def generate_pad_spiral(cx: float, cy: float, radius: float, nozzle_size: float, use_arc_moves: bool = False) -> list[tuple[float, float, bool, float, float]]:
    """Fill a circular pad with concentric circles from center outward."""
    points = []
    step       = nozzle_size * 0.8
    angle_step = 0.15

    r = step
    while r <= radius:
        if use_arc_moves:
            points.append((cx + r, cy, True, -r, 0))
            points.append((cx + r, cy, False, -r, 0))
        else:
            steps = int(2 * math.pi / angle_step) + 1
            for i in range(steps + 1):
                a = i * angle_step
                x = cx + r * math.cos(a)
                y = cy + r * math.sin(a)
                points.append((x, y, i == 0, 0, 0))
        r += step

    return points

def generate_pad_raster(cx, cy, size, nozzle_size, shape='C'):
    """Fill pad (Rectangle or Circle) with a continuous spiral."""
    
    if shape.startswith('RR:'):
        width  = size
        height = float(shape.split(':')[1])
    elif shape == 'R':
        width  = size
        height = size
    else:
        return []

    step   = nozzle_size * 0.8
    half_w = width / 2
    half_h = height / 2
    points = []

    num_layers = math.ceil(max(half_w, half_h) / step) + 1

    for layer in range(num_layers):
        w = max(half_w - layer * step, 0)
        h = max(half_h - layer * step, 0)

        next_w = max(half_w - (layer + 1) * step, 0)
        next_h = max(half_h - (layer + 1) * step, 0)

        if w == 0 and h == 0:
            break

        if layer == 0:
            points.append((cx - w, cy + h))

        if h > 0:
            points.append((cx + w, cy + h))   
            points.append((cx + w, cy - h))   
            points.append((cx - w, cy - h))   
            points.append((cx - w, cy + next_h))
            points.append((cx - next_w, cy + next_h))
        else:
            points.append((cx + w, cy))
            points.append((cx + next_w, cy))
            break

    segments = []
    for i in range(len(points) - 1):
        segments.append((points[i][0], points[i][1], points[i+1][0], points[i+1][1]))
    return segments

def camera_sweep(g, safe_z: float, board_size_x: float = 0, board_size_y: float = 0, layer_index: int = 0) -> bool:
    """Camera sweep after each ink + cure sequence."""
    g.rapid(z=safe_z)
    g.rapid(x=0, y=0)
    print(f"camera sweep layer {layer_index} (placeholder) — board {board_size_x}x{board_size_y}mm")
    return True

def deposit_insulator(g, coords: list, work_z: float, safe_z: float, nozzle_size: float, configFile: dict) -> None:
    """Deposit insulator layer (ACI SI3104) over all pad positions."""
    if not coords:
        print("  no coords for insulator layer, skipping")
        return

    insulator_head = get_head(configFile, "insulator")
    offset_x = insulator_head.get("offsetX", 0)
    offset_y = insulator_head.get("offsetY", 0)
    insulator_cure_seconds = insulator_head.get("cureSeconds", 600)

    print(f"  depositing insulator over {len(coords)} points")
    for x, y, *_ in coords:
        ox = max(0, x + offset_x)
        oy = max(0, y + offset_y)
        g.rapid(point=(ox, oy))
        g.move(z=work_z)
        g.rapid(z=safe_z)

    print(f"  insulator cure: 135C for {insulator_cure_seconds}s")
    g.write(f"M190 S{int(insulator_head.get('cureTemp', 135))}")
    g.sleep(insulator_cure_seconds)
    g.write("M140 S0")

def move_with_extrusion(g, x: float, y: float, from_x: float, from_y: float,
                         current_e: float, multiplier: float, enable_extrusion: bool) -> float:
    """Write a G1 move with optional E extrusion. Returns updated E value."""
    if enable_extrusion:
        dist   = math.sqrt((x - from_x)**2 + (y - from_y)**2)
        new_e  = current_e + dist * multiplier
        g.write(f"G1 X{x:.4f} Y{y:.4f} E{new_e:.5f}")
        return new_e
    else:
        g.move(point=(x, y))
        return current_e

def run(enable_tool_change=True, enable_heating=True, enable_camera_sweep=True, enable_crossover=True, use_arc_moves=False, enable_extrusion=False):
    """Main entry point — loads config, parses Gerber files, generates G-code."""

    # load machine and print settings from config.json
    with open(os.path.join(BASE_DIR, "config.json"), "r") as f:
        configFile = json.load(f)

    validate_config(configFile)

    extrusion_multiplier = configFile.get("extrusionMultiplier", 0.05)
    retraction_distance  = configFile.get("retractionDistance", 0.5)
    current_e            = 0.0

    # load head profiles
    conductive_head = get_head(configFile, "conductive")

    steps_per_mm_x   = configFile.get("steps_per_mm_x", 80)
    steps_per_mm_y   = configFile.get("steps_per_mm_y", 80)
    steps_per_mm_z   = configFile.get("steps_per_mm_z", 400)

    # derive output .gcode path from the zip path in config
    gerber_zip_path = configFile.get("gerberFile", "TestFiles/test-gbr.zip")
    project_root    = os.path.dirname(BASE_DIR)
    gerber_zip_full = os.path.join(project_root, gerber_zip_path)
    gerber_dir      = os.path.join(project_root, os.path.dirname(gerber_zip_path))
    gerber_name     = os.path.splitext(os.path.basename(gerber_zip_path))[0]
    output_file     = os.path.join(gerber_dir, gerber_name + ".gcode")
    extract_dir     = os.path.join(gerber_dir, gerber_name)
    os.makedirs(extract_dir, exist_ok=True)

    # unzip Gerber files before loading the job file
    if gerber_zip_path.endswith(".zip") and os.path.exists(gerber_zip_full):
        with zipfile.ZipFile(gerber_zip_full, "r") as z:
            z.extractall(extract_dir)
        print(f"Extracted {gerber_zip_path} to {extract_dir}")
    else:
        print(f"Skipping extraction — {gerber_zip_path} already extracted or not a zip")

    # if zip extracted into a single subfolder, use that as the scan dir
    entries = os.listdir(extract_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        extract_dir = os.path.join(extract_dir, entries[0])
        print(f"Using subfolder: {extract_dir}")

    # load the .gbrjob file
    gerber_job_file = configFile.get("gerberJobFile", "TestFiles/test-job.gbrjob")
    gerber_job_path = os.path.join(project_root, gerber_job_file)

    board_size_x  = 220
    board_size_y  = 220
    board_layers  = 2
    copper_thickness = 0.035
    files_to_process = []  # list of (gbr_path, layer_type, is_bottom)

    try:
        gerber_job = GerberJobFile.from_file(gerber_job_path)
        board_size_x     = gerber_job.general_specs.size.x
        board_size_y     = gerber_job.general_specs.size.y
        board_layers     = gerber_job.general_specs.layer_number
        copper_thickness = next(
            (s.thickness for s in gerber_job.material_stackup if s.type == "Copper"),
            0.035
        )
        for fa in gerber_job.files_attributes:
            layer_type = get_layer_type(fa.file_function)
            is_bottom  = "bot" in fa.file_function.lower()
            gbr_path   = os.path.join(project_root, os.path.dirname(gerber_zip_path), fa.path)
            files_to_process.append((gbr_path, layer_type, is_bottom))
        print(f"Board: {board_size_x}x{board_size_y}mm, {board_layers} layers (KiCad job file)")

    except Exception:
        print("Standard job file not found or invalid — falling back to filename detection")
        try:
            with open(gerber_job_path, "r") as f:
                job_data = json.load(f)
            overall = job_data.get("Overall", {})
            board_size_x     = overall.get("Size", {}).get("X", 220)
            board_size_y     = overall.get("Size", {}).get("Y", 220)
            board_layers     = overall.get("LayerNumber", 2)
            copper_thickness = overall.get("BoardThickness", 1.57) * 0.035 / 1.57
            print(f"Board: {board_size_x}x{board_size_y}mm, {board_layers} layers (Fusion job file)")
        except Exception:
            print("Could not parse job file — using defaults")

        for fname in sorted(os.listdir(extract_dir)):
            if os.path.splitext(fname.lower())[1] not in GERBER_EXTENSIONS:
                continue
            layer_type_full = get_layer_type_from_filename(fname)
            if layer_type_full == "unknown":
                print(f"  skipping {fname} (unknown layer type)")
                continue
            
            if "copper" in layer_type_full:
                layer_type = "copper"
            elif "paste" in layer_type_full:
                layer_type = "paste"
            elif "mask" in layer_type_full:
                layer_type = "mask"
            elif "silkscreen" in layer_type_full:
                layer_type = "silkscreen"
            elif "edge" in layer_type_full:
                layer_type = "edge"
            else:
                layer_type = "unknown"

            is_bottom = "bottom" in layer_type_full or "bot" in layer_type_full
            gbr_path = os.path.join(extract_dir, fname)
            files_to_process.append((gbr_path, layer_type, is_bottom))

    # --- G-Code Generation Phase ---
    with GCodeBuilder(output=output_file) as g:
        g.write("; --- BEGIN PRINT ---")
        g.write("G90 ; absolute coordinates")
        g.write("M82 ; absolute extrusion")

        layer_mode = configFile.get("layerMode", "single")

        for gbr_path, layer_type, is_bottom in files_to_process:
            fname = os.path.basename(gbr_path)

            if layer_type not in ["copper", "paste", "insulator"]:
                print(f"  skipping {fname} ({layer_type})")
                continue

            if layer_mode == "single" and is_bottom:
                print(f"  skipping {fname} (bottom layer in single mode)")
                continue

            print(f"\n--- Processing Layer: {fname} ({layer_type}) ---")

            if layer_type == "copper":
                offset_x = 10
                offset_y = 10
                min_trace_width = conductive_head.get("traceWidth", 0.225)
                nozzle_size = conductive_head.get("nozzleSize", 0.225)

                raw_segments, min_x, min_y = extract_traces(
                    gbr_path, offset_x=offset_x, offset_y=offset_y, min_trace_width=min_trace_width
                )
                pads, _, _ = extract_coords(gbr_path, offset_x=offset_x, offset_y=offset_y)

                if raw_segments:
                    print(f"  extracted {len(raw_segments)} trace segments")
                    layer_toolpaths = generate_shapely_toolpaths(raw_segments, nozzle_size)

                    for path in layer_toolpaths:
                        if not path or len(path) < 2:
                            continue
                        start_x, start_y = path[0]
                        g.rapid(z=5)
                        g.rapid(point=(start_x, start_y))
                        g.move(z=0.2)
                        for x, y in path[1:]:
                            current_e = move_with_extrusion(g, x, y, start_x, start_y, current_e, extrusion_multiplier, enable_extrusion)
                            start_x, start_y = x, y
                        if enable_extrusion:
                            current_e -= retraction_distance
                            g.write(f"G1 E{current_e:.5f} F1800")

                if pads:
                    print(f"  extracted {len(pads)} pads")
                    for px, py, size, shape in pads:
                        if shape == 'C':
                            circles = generate_pad_spiral(px, py, size / 2, nozzle_size, use_arc_moves=use_arc_moves)
                            g.rapid(z=5)
                            g.rapid(point=(circles[0][0], circles[0][1]))
                            g.move(z=0.2)
                            last_x, last_y = circles[0][0], circles[0][1]
                            for cx, cy, new_circle, arc_i, arc_j in circles:
                                if new_circle:
                                    g.rapid(z=5)
                                    g.rapid(point=(cx, cy))
                                    g.move(z=0.2)
                                elif use_arc_moves and arc_i != 0:
                                    g.write(f"G2 X{cx:.4f} Y{cy:.4f} I{arc_i:.4f} J{arc_j:.4f}")
                                else:
                                    current_e = move_with_extrusion(g, cx, cy, last_x, last_y, current_e, extrusion_multiplier, enable_extrusion)
                                last_x, last_y = cx, cy
                            g.rapid(z=5)
                        else:
                            segments = generate_pad_raster(px, py, size, nozzle_size, shape=shape)
                            if segments:
                                g.rapid(z=5)
                                g.rapid(point=(segments[0][0], segments[0][1]))
                                g.move(z=0.2)
                                last_x, last_y = segments[0][0], segments[0][1]
                                for sx, sy, ex, ey in segments:
                                    current_e = move_with_extrusion(g, ex, ey, sx, sy, current_e, extrusion_multiplier, enable_extrusion)
                                    last_x, last_y = ex, ey
                                g.rapid(z=5)

            elif layer_type == "paste":
                pass

            elif layer_type == "insulator":
                pass

        # Clean up and end print
        g.write("\n; --- END PRINT ---")
        g.write("G28 X0 Y0 ; home X and Y")
        g.write("M84 ; disable motors")

    print(f"\nSuccess! G-code saved to {output_file}")


if __name__ == "__main__":
    run(enable_extrusion=True)