from __future__ import annotations
import os
import json
import re
import math
import zipfile
from shapely.geometry import Point
from pygerber.gerber.api import GerberFile, GerberJobFile
from gscrib import GCodeBuilder

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union


GERBER_EXTENSIONS = {".gbr", ".gtl", ".gbl", ".gts", ".gbs", ".gto",
                     ".gbo", ".gtp", ".gbp", ".gko", ".ger"}

# absolute path to the src/ folder so all file paths work regardless of where you run the script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ═══════════════════════════════════════════════════════════════════════
#  TUNABLE CONSTANTS — machine and process values not exposed in config.json.
#  Change them here; everything below references these names.
#  (Per-print values like work heights, nozzle sizes, and pasteDotE live in
#   config.json / the GUI, not here.)
# ═══════════════════════════════════════════════════════════════════════

# default nozzle size (mm) used only when config.json lacks a head definition
DEFAULT_NOZZLE = 0.2

# ── board placement ──
BOARD_MARGIN = 2.0            # mm gap between machine origin and board min corner

# ── end-of-ink-layer behavior ──
INK_FINAL_PULL_E    = 400     # big E pull to fully stop ink flow after a layer
INK_FINAL_PULL_FEED = 200     # feed rate for that pull (mm/min)
PARK_X, PARK_Y, PARK_Z = 0, -20, 150   # where the head parks during cure/heating
COOLDOWN_WAIT_S = 1200        # post-cure cool-down dwell (20 min)

# ── paste dot dispensing ──
PASTE_WIGGLE = 0.2            # mm lateral wiggle after each dot to shear the paste off the tip

# ── extrusion-mode retract ──
RETRACT_FEED = 1800           # feed rate for the retraction move after each path

# ── prime (purge) sequence defaults — see prime_lead_screw() ──
PRIME_LIFT_Z      = 5.5       # Z lift for lead screw engagement (relative baseline)
PRIME_EXTRUDE     = 20        # E amount per prime push
PRIME_FEED        = 200       # feed rate of prime pushes (mm/min)
PRIME_CYCLES      = 20        # number of mini-extrude cycles
PRIME_CYCLE_DELAY = 2.5       # seconds between cycles
PRIME_SETTLE_S    = 10        # settle wait after priming
PRIME_TRAVEL_FEED = 20000     # rapid feed for the initial prime lift
WIPE_X, WIPE_Y = 5, 190       # wipe station location on the bed
WIPE_LENGTH  = 10             # mm of the single nozzle-wipe stroke after the purge

# ── camera sweep ──
CAMERA_SAFE_Z_DEFAULT = 50.0  # fallback camera height if not passed in
CAMERA_GRID_SPACING   = 2.5   # mm between capture points (rows and columns)
CAMERA_SETTLE_S       = 3     # dwell before the very first capture of a sweep

# ── G2/G3 arc fitting (use_arc_moves) ──
ARC_TOLERANCE   = 0.002       # max deviation (mm) for points to count as an arc
ARC_MIN_POINTS  = 5           # min consecutive points before attempting a fit
ARC_MAX_WINDOW  = 20          # max points considered per arc

# ── LAYER ORDER ──────────────────────────────────────────────
# Layer 1    - Conductive ink (Conductor 3, face up)       Z=0.2
# Layer 1.5  - Cure stage 1: dry 90°C for 5min
# Layer 1.75 - Cure stage 2: sinter 170°C for 15min
# Layer 2    - Camera sweep
# --- single layer boards stop here ---
# Layer 3    - Insulator                                   Z=0.4
# Layer 3.5  - Cure
# Layer 4    - Camera sweep (check for shorts)
# --- single side boards stop here ---
# Layer 5    - Conductive ink (crossover)                  Z=0.6
# Layer 5.5  - Cure stage 1: dry 90°C for 5min
# Layer 5.75 - Cure stage 2: sinter 170°C for 15min
# Layer 6    - Camera sweep
#
# Note: Conductor 3 — no burnishing needed, no flipping needed
# Note: insulator cover type determines if stop is at layer 3.5 or 4

def generate_shapely_toolpaths(raw_segments, nozzle_size, pads=None, stepover_ratio=0.85, subtract_rect_pads=False, edge_inset=0.0):
    """
    Merges overlapping trace segments and generates concentric fill paths.
    raw_segments: list of ((start_x, start_y), (end_x, end_y), trace_width)
    pads: optional list of (x, y, size, shape) to subtract from trace geometry
    subtract_rect_pads: subtract rect pads from traces instead of unioning them
        (ink-traces-only mode, where pads are drawn by the paste head)
    Returns a list of continuous paths (each path is a list of (x,y) tuples).
    """
    if not raw_segments:
        return []

    trace_polys = []
    for (sx, sy), (ex, ey), width in raw_segments:
        line = LineString([(sx, sy), (ex, ey)])
        buffer_amount = 0.05
        if (width / 2.0 - edge_inset >= 0.01):
            buffer_amount = width / 2.0 - edge_inset
        else:
            buffer_amount = 0.01
        poly = line.buffer(buffer_amount, cap_style=1, join_style=1)
        trace_polys.append(poly)

    merged_layer = unary_union(trace_polys)

    # subtract pad areas so traces stop flush at pad boundaries
    if pads:
        shrink = nozzle_size / 2
        subtract_polys = []
        union_polys = []
        for x, y, size, shape in pads:
            if shape == 'C':
                subtract_polys.append(Point(x, y).buffer(max(0, size / 2 - shrink)))
            elif shape.startswith('RR:'):
                h = float(shape.split(':')[1])
                w = size
                if subtract_rect_pads:
                    sw = max(0, w / 2 - shrink)
                    sh = max(0, h / 2 - shrink)
                    if sw > 0 and sh > 0:
                        subtract_polys.append(Polygon([
                            (x - sw, y - sh), (x + sw, y - sh),
                            (x + sw, y + sh), (x - sw, y + sh)
                        ]))
                else:
                    union_polys.append(Polygon([
                        (x - w/2, y - h/2), (x + w/2, y - h/2),
                        (x + w/2, y + h/2), (x - w/2, y + h/2)
                    ]))
            elif shape == 'R':
                half = size / 2
                if subtract_rect_pads:
                    sh = max(0, half - shrink)
                    if sh > 0:
                        subtract_polys.append(Polygon([
                            (x - sh, y - sh), (x + sh, y - sh),
                            (x + sh, y + sh), (x - sh, y + sh)
                        ]))
                else:
                    union_polys.append(Polygon([
                        (x - half, y - half), (x + half, y - half),
                        (x + half, y + half), (x - half, y + half)
                    ]))
        if union_polys:
            merged_layer = unary_union([merged_layer] + union_polys)
        if subtract_polys:
            merged_layer = merged_layer.difference(unary_union(subtract_polys))

    toolpaths = []
    current_inset = nozzle_size / 2.0 + edge_inset
    stepover_dist = nozzle_size * stepover_ratio

    first_pass = True
    # while True:
    #     path_geo = merged_layer #.buffer(-current_inset, join_style=2)
    #     if first_pass and pads and union_polys:
    #         # after the seamless outer pass, exclude rect pad interiors (raster fills them)
    #         merged_layer = merged_layer.difference(unary_union(union_polys))
    #         first_pass = False
    #     if path_geo.is_empty:
    #         break
    geoms = merged_layer.geoms if hasattr(merged_layer, 'geoms') else [merged_layer]
    for geom in geoms:
        if isinstance(geom, Polygon):
            toolpaths.append(list(geom.exterior.coords))
            for interior in geom.interiors:
                toolpaths.append(list(interior.coords))
    #     current_inset += stepover_dist

    return toolpaths

# ─────────────────────────────────────────────────────────────────────────
# Path ordering — greedy nearest-neighbor to cut G0 travel between paths
# ─────────────────────────────────────────────────────────────────────────
def order_paths_nearest(toolpaths):
    """Greedy nearest-neighbor ordering of toolpaths to minimize G0 travel."""
    if len(toolpaths) <= 2:
        return toolpaths
    remaining = list(toolpaths)
    ordered   = [remaining.pop(0)]
    while remaining:
        lx, ly = ordered[-1][-1][0], ordered[-1][-1][1]
        best_i = min(range(len(remaining)),
                     key=lambda i: (remaining[i][0][0]-lx)**2 + (remaining[i][0][1]-ly)**2)
        ordered.append(remaining.pop(best_i))
    return ordered

# ─────────────────────────────────────────────────────────────────────────
# Config loading / validation — fail fast on bad or corrupted config.json
# ─────────────────────────────────────────────────────────────────────────
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

    for z_key in ("copperWorkZ", "insulatorWorkZ", "crossoverWorkZ", "pasteWorkZ"):
        v = config.get(z_key)
        if not isinstance(v, (int, float)):
            errors.append(f"{z_key} must be a number (got {v!r})")

    if config.get("layerMode", "single") not in ["single", "multi"]:
        errors.append("layerMode must be 'single' or 'multi'")

    heads  = config.get("heads", [])
    active = config.get("activeHeads", [])
    tools  = {}
    for head in heads:
        if head.get("id") in active:
            tn = head.get("toolNumber")
            if tn in tools:
                errors.append(f"duplicate toolNumber {tn}: heads '{tools[tn]}' and '{head.get('id')}'")
            else:
                tools[tn] = head.get("id")

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
            "nozzleSize": configFile.get("nozzleSize", DEFAULT_NOZZLE),
            "traceWidth": configFile.get("traceWidth", DEFAULT_NOZZLE),
            "cureDryTemp": configFile.get("cure_dry_temp", 90),
            "cureDrySeconds": configFile.get("cure_dry_seconds", 300),
            "cureTemp": configFile.get("cure_temp", 170),
            "cureSeconds": configFile.get("cure_seconds", 900)
        }
    if head_type == "insulator":
        return {
            "nozzleSize": configFile.get("nozzleSize", DEFAULT_NOZZLE),
            "cureTemp": configFile.get("insulator_cure_temp", 135),
            "cureSeconds": configFile.get("insulator_cure_seconds", 600),
            "offsetX": configFile.get("insulator_head_offset_x", 0),
            "offsetY": configFile.get("insulator_head_offset_y", 0)
        }
    if head_type == "camera":
        return {
            "toolNumber": configFile.get("cameraToolNumber", 2)
        }
    if head_type == "paste":
        return {
            "toolNumber": configFile.get("pasteToolNumber", 1),
            "nozzleSize": configFile.get("pasteNozzleSize", 0.3),
            "dwellFactor": configFile.get("pasteDwellFactor", 0.5),
            "cureTemp": 0,
            "cureSeconds": 0
        }
    return {}

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
    if any(x in f for x in ["copper_crossover", "crossover"]):
        return "copper_crossover"
    if any(x in f for x in ["copper_bottom", "b_cu", "bottom_copper", "gbl"]):
        return "copper_bottom"
    if any(x in f for x in ["insulator"]):
        return "insulator"
    if any(x in f for x in ["paste_bottom", "b_paste", "bottom_paste", "gbp"]):
        return "paste_bottom"
    elif any(x in f for x in ["paste_top", "f_paste", "top_paste", "gtp", "paste"]):
        return "paste_top"
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


# ─────────────────────────────────────────────────────────────────────────
# Gerber parsing — pads (extract_coords) and traces (extract_traces).
# Coordinates are normalized so the board min corner sits at the global
# origin computed in compute_global_origin() (shared across all layers).
# ─────────────────────────────────────────────────────────────────────────
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
                r      = abs(float(params[0]))  # corner radius extends beyond corner points
                dxs    = [abs(float(params[i])) for i in range(1, len(params) - 1, 2)]
                dys    = [abs(float(params[i])) for i in range(2, len(params) - 1, 2)]
                width  = (max(dxs) + r) * 2 * scale
                height = (max(dys) + r) * 2 * scale
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

    # --- G36/G37 filled regions → treat as rectangular pads ---
    for region_match in re.finditer(r'G36\*(.*?)G37\*', source, re.DOTALL):
        block = region_match.group(1)
        pts = [(int(m.group(1)) / divisor * scale, int(m.group(2)) / divisor * scale)
               for m in re.finditer(r'X(-?\d+)Y(-?\d+)D0[12]\*', block)]
        if len(pts) >= 4:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            w  = max(xs) - min(xs)
            h  = max(ys) - min(ys)
            cx = (max(xs) + min(xs)) / 2
            cy = (max(ys) + min(ys)) / 2
            if w > 0 and h > 0:
                raw.append((cx, cy, w, f'RR:{h}'))

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

def extract_traces(gbr_path: str, offset_x: float = None, offset_y: float = None, min_trace_width: float = DEFAULT_NOZZLE):
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
            aperture_sizes[apt_id] = float(params[0]) * scale if params else DEFAULT_NOZZLE

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

def compute_global_origin(files_to_process: list, margin: float = BOARD_MARGIN, nozzle_size: float = DEFAULT_NOZZLE) -> tuple[float, float]:
    """
    Compute a single shared origin across all layers by finding the global
    minimum X/Y coordinate across every Gerber file. This ensures all layers
    align correctly in G-code space — critical for insulator pads that have
    only one point and would otherwise normalize to (0,0) independently.
    """
    global_min_x = float('inf')
    global_min_y = float('inf')

    for gbr_path, layer_type, is_bottom in files_to_process:
        if layer_type not in ["copper", "copper_top", "insulator", "copper_crossover"]:
            continue
        # check traces
        try:
            segs, raw_min_x, raw_min_y = extract_traces(gbr_path)
            if segs:
                global_min_x = min(global_min_x, raw_min_x)
                global_min_y = min(global_min_y, raw_min_y)
        except Exception:
            pass
        # check pads
        try:
            coords, pad_min_x, pad_min_y = extract_coords(gbr_path)
            if coords:
                global_min_x = min(global_min_x, pad_min_x)
                global_min_y = min(global_min_y, pad_min_y)
        except Exception:
            pass

    if global_min_x == float('inf'):
        global_min_x = 0.0
    if global_min_y == float('inf'):
        global_min_y = 0.0

    offset_x = global_min_x - (margin + nozzle_size)
    offset_y = global_min_y - (margin + nozzle_size)
    print(f"Global origin: raw_min=({global_min_x:.3f}, {global_min_y:.3f})  offset=({offset_x:.3f}, {offset_y:.3f})")
    return offset_x, offset_y

# ─────────────────────────────────────────────────────────────────────────
# Pad fill generators — spiral for circular pads, concentric rects for
# rectangular pads. Tunables (stepover ratios, insets) are inline in these
# functions and are part of the toolpath math — change with care.
# ─────────────────────────────────────────────────────────────────────────
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

def generate_pad_raster(cx, cy, size, nozzle_size, shape='C', inset=None, fill_center=False, inset_y=None):
    """Fill pad with concentric rectangles, returned as separate paths."""

    if shape.startswith('RR:'):
        width  = size
        height = float(shape.split(':')[1])
    elif shape == 'R':
        width  = size
        height = size
    else:
        return []

    step   = nozzle_size * 0.7
    if inset is None:
        inset = nozzle_size / 2 + nozzle_size * 0.8  # pull first pass in so bead edge meets true pad boundary
    if inset_y is None:
        inset_y = inset
    half_w = max(width / 2 - inset, 0)
    half_h = max(height / 2 - inset_y, 0)
    all_rects = []

    num_layers = math.ceil(max(half_w, half_h) / step) + 1

    for layer in range(num_layers):
        w = max(half_w - layer * step, 0)
        h = max(half_h - layer * step, 0)

        if w == 0 and h == 0:
            break
        if layer > 0 and (w < step / 2 or h < step / 2):
            if fill_center and max(w, h) >= step / 2:
                # one dimension collapsed but the other still has material:
                # finish with a straight stripe down the remaining center strip
                if w >= h:
                    all_rects.append([(cx - w, cy, cx + w, cy)])
                else:
                    all_rects.append([(cx, cy - h, cx, cy + h)])
            break

        rect_points = [
            (cx - w, cy + h),
            (cx + w, cy + h),
            (cx + w, cy - h),
            (cx - w, cy - h),
            (cx - w, cy + h),
        ]
        segments = [(rect_points[i][0], rect_points[i][1],
                     rect_points[i+1][0], rect_points[i+1][1])
                    for i in range(len(rect_points) - 1)]
        all_rects.append(segments)

    return all_rects

# ─────────────────────────────────────────────────────────────────────────
# Camera sweep — serpentine capture grid with a backlash jiggle at each row
# start; M118/M240 markers are consumed by the vision pipeline (Andrew).
# ─────────────────────────────────────────────────────────────────────────
def emit_gerber_transfer(g, files_to_process):
    """Stream the top copper gerber to the vision system over M118."""
    g.write("; --- GERBER FILE TRANSFER ---")
    for gbr_path, layer_type, is_bottom in files_to_process:
        if layer_type == "copper" and not is_bottom:
            g.write("M118 BEGIN_FILE_TRANSFER")
            with open(gbr_path, "r") as top_copper_gbr_file:
                for line in top_copper_gbr_file:
                    g.write("M118 " + line)
            g.write("M118 END_FILE_TRANSFER")
            break


def camera_sweep(g, safe_z:float=CAMERA_SAFE_Z_DEFAULT, board_size_x: float = 0, board_size_y: float = 0,
                 layer_index: int = 0, camera_head_tool: int = 3,
                 row_spacing: float = CAMERA_GRID_SPACING, column_spacing: float = CAMERA_GRID_SPACING,
                 origin_x: float = 0, origin_y: float = 0) -> bool:
    """Camera sweep after each ink + cure sequence."""
    print(f"  camera sweep layer {layer_index} — board {board_size_x:.1f}x{board_size_y:.1f}mm")

    g.write(f"T{camera_head_tool} ; camera head")
    g.write(f"M118 START_LAYER {layer_index}")

    num_rows    = max(1, math.ceil(board_size_y / row_spacing)) + 1
    num_columns = max(1, math.ceil(board_size_x / column_spacing)) + 1

    for row in range(num_rows):
        y_pos = origin_y + row * row_spacing
        # jiggle at the start of every row: move to the row start, then out
        # one spacing on both X and Y together; the first column move below
        # serves as the return
        g.write(f"G0 X{origin_x:.3f} Y{y_pos:.3f} Z{safe_z:.3f}")
        g.write(f"G0 X{origin_x + column_spacing:.3f} Y{y_pos + row_spacing:.3f} Z{safe_z:.3f}")
        for col in range(num_columns):
            x_pos = origin_x + col * column_spacing
            g.write(f"G0 X{x_pos:.3f} Y{y_pos:.3f} Z{safe_z:.3f}")
            if row == 0 and col == 0:
                g.write(f"G4 S{CAMERA_SETTLE_S} ; settle wait after first jiggle")
            g.write("M240 ; camera capture")
        g.write(f"G0 X{origin_x:.3f} Y{y_pos:.3f} Z{safe_z:.3f}")
        g.write("M118 NEW_ROW")

    g.write(f"M118 END_LAYER {layer_index}")
    return True

def move_with_extrusion(g, x: float, y: float, from_x: float, from_y: float,
                         current_e: float, flow_rate: float, layer_height: float,
                         trace_width: float, enable_extrusion: bool) -> float:
    """Write a G1 move with optional E extrusion. Returns updated E value."""
    if enable_extrusion:
        dist  = math.sqrt((x - from_x)**2 + (y - from_y)**2)
        new_e = current_e + dist * trace_width * layer_height * flow_rate
        g.write(f"G1 X{x:.4f} Y{y:.4f} E{new_e:.5f}")
        return new_e
    else:
        g.move(point=(x, y))
        return current_e

def fit_arc_to_points(points, tolerance=0.01):
    """Try to fit a circle to 3+ points. Returns (cx, cy, r) or None if not circular."""
    if len(points) < 3:
        return None
    p1, p2, p3 = points[0], points[len(points)//2], points[-1]
    ax, ay = p1; bx, by = p2; cx, cy = p3
    d = 2 * (ax*(by-cy) + bx*(cy-ay) + cx*(ay-by))
    if abs(d) < 1e-10:
        return None
    ux = ((ax**2+ay**2)*(by-cy) + (bx**2+by**2)*(cy-ay) + (cx**2+cy**2)*(ay-by)) / d
    uy = ((ax**2+ay**2)*(cx-bx) + (bx**2+by**2)*(ax-cx) + (cx**2+cy**2)*(bx-ax)) / d
    r = math.sqrt((ax-ux)**2 + (ay-uy)**2)
    if r > 50.0:
        return None
    for px, py in points:
        if abs(math.sqrt((px-ux)**2 + (py-uy)**2) - r) > tolerance:
            return None
    # reject shapes whose vertices happen to lie on a circle (e.g. squares):
    # midpoints of a true arc's segments also lie on the circle, a polygon's don't
    for (x1, y1), (x2, y2) in zip(points[:-1], points[1:]):
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        if abs(math.sqrt((mx-ux)**2 + (my-uy)**2) - r) > tolerance:
            return None
    ex, ey = points[-1]
    chord_len = math.sqrt((ex - ax)**2 + (ey - ay)**2)
    if chord_len < 1e-10:
        return None
    max_sag = 0.0
    for px, py in points:
        sag = abs((ey - ay) * px - (ex - ax) * py + ex * ay - ey * ax) / chord_len
        if sag > max_sag:
            max_sag = sag
    straight_threshold = max(tolerance, chord_len * 0.005)
    if max_sag < straight_threshold:
        return None
    return (ux, uy, r)

def points_to_gcode_path(g, path, current_e, flow_rate, layer_height, trace_width,
                          enable_extrusion, use_arc_moves, arc_tolerance=ARC_TOLERANCE,
                          min_arc_points=ARC_MIN_POINTS, work_z=0.2, pullpush=30.0, pullpush_speed=200, lift_z=5):
    """Write a Shapely path to G-code, replacing arc segments with G2/G3 where possible."""
    if not path or len(path) < 2:
        return current_e

    start_x, start_y = path[0]
    if pullpush > 0:
        g.move(e=-pullpush, f=pullpush_speed)
    g.rapid(z=lift_z)
    g.rapid(point=(start_x, start_y))
    g.rapid(z=work_z)
    if pullpush > 0:
        g.move(e=pullpush, f=pullpush_speed)

    i = 1
    while i < len(path):
        if use_arc_moves:
            best_arc = None
            for end in range(i + min_arc_points, min(i + ARC_MAX_WINDOW, len(path)) + 1):
                window = [(path[j][0], path[j][1]) for j in range(i-1, end)]
                result = fit_arc_to_points(window, tolerance=arc_tolerance)
                if result:
                    best_arc = (end, result)
                else:
                    break
            if best_arc:
                end_idx, (cx, cy, r) = best_arc
                ex, ey = path[end_idx - 1]
                fx, fy = start_x, start_y
                cross = (cx - fx) * (ey - fy) - (cy - fy) * (ex - fx)
                clockwise = cross > 0
                arc_cmd = "G2" if clockwise else "G3"
                ix = cx - fx
                iy = cy - fy
                if enable_extrusion:
                    dist = math.sqrt((ex - fx)**2 + (ey - fy)**2)
                    current_e += dist * trace_width * layer_height * flow_rate
                    g.write(f"{arc_cmd} X{ex:.4f} Y{ey:.4f} I{ix:.4f} J{iy:.4f} E{current_e:.5f}")
                else:
                    g.write(f"{arc_cmd} X{ex:.4f} Y{ey:.4f} I{ix:.4f} J{iy:.4f}")
                start_x, start_y = ex, ey
                i = end_idx
                continue

        x, y = path[i]
        current_e = move_with_extrusion(g, x, y, start_x, start_y, current_e, flow_rate, layer_height, trace_width, enable_extrusion)
        start_x, start_y = x, y
        i += 1

    return current_e

# ─────────────────────────────────────────────────────────────────────────
# Prime / purge — seats the lead-screw piston against the ink and runs
# repeated mini-extrudes so dispensing starts consistently. Tunables at top.
# ─────────────────────────────────────────────────────────────────────────
def nozzle_wipe(g, work_z, lift_z):
    """Travel to the wipe station and drag the nozzle through it once (all rapids)."""
    g.rapid(z=lift_z)
    g.rapid(point=(WIPE_X, WIPE_Y))
    g.rapid(z=work_z)
    g.rapid(x=WIPE_X + WIPE_LENGTH)
    g.rapid(z=lift_z)


def prime_lead_screw(g, lift_z=PRIME_LIFT_Z, work_z=0.2, extrude_amount=PRIME_EXTRUDE, extrude_feed=PRIME_FEED,
                     prime_cycles=PRIME_CYCLES, cycle_delay_s=PRIME_CYCLE_DELAY, settle_s=PRIME_SETTLE_S):
    """
    Prime the lead screw at startup — lifts Z, pushes piston down to seat
    against ink, then runs repeated mini-extrudes to prep for dispensing.
    """
    print("Priming lead screw...")
    g.write(f"G0 Z{lift_z} F{PRIME_TRAVEL_FEED} ; lift Z for lead screw engagement")
    g.write("G0 X1 Y1")
    g.write(f"G1 E{extrude_amount} F{extrude_feed} ; initial piston seat")

    for i in range(prime_cycles):
        g.write(f"G1 E{extrude_amount} F{extrude_feed} ; prime cycle {i + 1}/{prime_cycles}")
        g.write(f"G4 S{cycle_delay_s} ; wait {cycle_delay_s}s")

    g.write(f"G4 S{settle_s} ; settle wait")

    # nozzle wipe at the wipe station
    nozzle_wipe(g, work_z=work_z, lift_z=lift_z)
    print("Lead screw primed")

# ─────────────────────────────────────────────────────────────────────────
# Main entry point. Layer order: copper -> (cure, camera sweep) -> paste ->
# insulator -> crossover. Z scheme: each layer works at <layer>WorkZ +
# substrateHeight (absolute carriage coords incl. head length), and travels
# at workZ + hopClearance. All of those come from config.json.
# ─────────────────────────────────────────────────────────────────────────
def run(enable_tool_change=True, enable_heating=True, enable_camera_sweep=True,
        enable_crossover=True, use_arc_moves=False, enable_extrusion=False, enable_purge=True, enable_conductive=True, enable_paste=True,
        ink_traces_only=False, enable_gerber_transfer=True, enable_second_ink_layer=False):
    """Main entry point — loads config, parses Gerber files, generates G-code."""
    print("=== NEW SLICER v2 ===")

    with open(os.path.join(BASE_DIR, "config.json"), "r") as f:
        configFile = json.load(f)

    validate_config(configFile)

    retraction_distance = configFile.get("retractionDistance", 0.5)
    global_pullpush       = configFile.get("pullpush", 10)
    global_pullpush_speed = configFile.get("pullpush_speed", 200)
    paste_pullpush          = configFile.get("paste_pullpush", 60)
    paste_pullpush_speed    = configFile.get("paste_pullpush_speed", 200)
    current_e           = 0.0

    # Z heights for each layer — configurable, with sensible defaults
    substrate_height = configFile.get("substrateHeight", 1.5)
    copper_work_z    = configFile.get("copperWorkZ") + substrate_height
    insulator_work_z = configFile.get("insulatorWorkZ") + substrate_height
    crossover_work_z = configFile.get("crossoverWorkZ") + substrate_height
    paste_work_z     = configFile.get("pasteWorkZ") + substrate_height
    camera_work_z  = configFile.get("cameraWorkZ", 50) + substrate_height
    hop_clearance  = configFile.get("hopClearance", 5)  # travel lift above each head's work height
    copper_hop_z    = copper_work_z    + hop_clearance
    insulator_hop_z = insulator_work_z + hop_clearance
    crossover_hop_z = crossover_work_z + hop_clearance
    paste_hop_z     = paste_work_z     + hop_clearance
    base_copper_work_z = copper_work_z   # for optional second-ink-layer Z offset
    copper_passes_done = 0
    total_copper_passes = 1  # becomes 2 when the second ink layer is enabled

    print_feed_rate = configFile.get("printFeedRate", 3600)
    trace_edge_inset = configFile.get("traceEdgeInset", 0.05)

    camera_head        = get_head(configFile, "camera")
    camera_tool_number = camera_head.get("toolNumber", 2)
    conductive_head    = get_head(configFile, "conductive")
    insulator_head     = get_head(configFile, "insulator")

    steps_per_mm_x = configFile.get("steps_per_mm_x", 80)
    steps_per_mm_y = configFile.get("steps_per_mm_y", 80)
    steps_per_mm_z = configFile.get("steps_per_mm_z", 400)

    gerber_zip_path = configFile.get("gerberFile", "TestFiles/test-gbr.zip")
    project_root    = os.path.dirname(BASE_DIR)
    gerber_zip_full = os.path.join(project_root, gerber_zip_path)
    gerber_dir      = os.path.join(project_root, os.path.dirname(gerber_zip_path))
    gerber_name     = os.path.splitext(os.path.basename(gerber_zip_path))[0]
    output_file     = os.path.join(gerber_dir, gerber_name + ".gcode")
    extract_dir     = os.path.join(gerber_dir, gerber_name)
    os.makedirs(extract_dir, exist_ok=True)

    if gerber_zip_path.endswith(".zip") and os.path.exists(gerber_zip_full):
        with zipfile.ZipFile(gerber_zip_full, "r") as z:
            z.extractall(extract_dir)
        print(f"Extracted {gerber_zip_path} to {extract_dir}")
    else:
        print(f"Skipping extraction — {gerber_zip_path} already extracted or not a zip")

    entries = os.listdir(extract_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        extract_dir = os.path.join(extract_dir, entries[0])
        print(f"Using subfolder: {extract_dir}")

    gerber_job_file = configFile.get("gerberJobFile", "TestFiles/test-job.gbrjob")
    gerber_job_path = os.path.join(project_root, gerber_job_file)

    board_size_x     = 220
    board_size_y     = 220
    board_layers     = 2
    copper_thickness = 0.035   # parsed but currently unused; kept for future stackup logic
    files_to_process = []

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

            if "copper_crossover" in layer_type_full:
                layer_type = "copper_crossover"
            elif "copper" in layer_type_full:
                layer_type = "copper"
            elif "insulator" in layer_type_full:
                layer_type = "insulator"
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

    # sort files by explicit layer print order
    LAYER_ORDER = {
        "copper": 0,
        "copper_top": 0,
        "insulator": 1,
        "copper_crossover": 2,
        "copper_bottom": 3,
    }
    files_to_process.sort(key=lambda x: LAYER_ORDER.get(x[1], 99))

    # ── GLOBAL ORIGIN — compute once, shared across all layers ──
    margin      = BOARD_MARGIN
    nozzle_size = conductive_head.get("nozzleSize", DEFAULT_NOZZLE)
    global_offset_x, global_offset_y = compute_global_origin(
        files_to_process, margin=margin, nozzle_size=nozzle_size
    )

    # second ink layer: duplicate the top copper entry so the identical
    # copper pass (traces + pads + cure) runs twice back to back
    # the toggle or the config key can enable it; Z offset for the second
    # pass comes from config (0 = literally identical G-code both passes)
    second_ink_z_offset = configFile.get("secondInkLayerZOffset", 0)
    if enable_second_ink_layer or configFile.get("secondInkLayer", False):
        enable_second_ink_layer = True
        for i, entry in enumerate(files_to_process):
            if entry[1] == "copper" and not entry[2]:
                files_to_process.insert(i + 1, entry)
                print("Second ink layer enabled — copper layer will print twice"
                      + (f" (second pass Z +{second_ink_z_offset})" if second_ink_z_offset else ""))
                total_copper_passes = 2
                break

    # --- G-Code Generation Phase ---
    with GCodeBuilder(output=output_file) as g:
        g.write("; --- BEGIN PRINT ---")
        g.write("G21 ; set units to millimeters")
        g.write("G90 ; absolute coordinates")
        g.write("M82 ; absolute extrusion")
        g.write("G28 ; home all axes")
        g.write("G92 E0 ; reset E axis")
        g.write(f"F{print_feed_rate} ; set print feed rate")
        if enable_tool_change:
            g.write("T0 ; conductive head")
            # wipe the freshly loaded nozzle before any printing
            nozzle_wipe(g, work_z=copper_work_z, lift_z=copper_hop_z)

        layer_mode = configFile.get("layerMode", "single")

        for gbr_path, layer_type, is_bottom in files_to_process:
            fname = os.path.basename(gbr_path)

            if layer_type not in ["copper", "paste", "insulator", "copper_crossover"]:
                print(f"  skipping {fname} ({layer_type})")
                continue

            if layer_mode == "single" and is_bottom:
                print(f"  skipping {fname} (bottom layer in single mode)")
                continue

            print(f"\n--- Processing Layer: {fname} ({layer_type}) ---")

            if layer_type == "copper":
                if not enable_conductive:
                    print(f"  skipping {fname} (conductive ink disabled)")
                    continue
                # second copper pass prints one ink-layer higher if configured
                if copper_passes_done > 0 and second_ink_z_offset:
                    copper_work_z = base_copper_work_z + copper_passes_done * second_ink_z_offset
                    copper_hop_z  = copper_work_z + hop_clearance
                    print(f"  second ink pass at Z{copper_work_z}")
                copper_passes_done += 1
                
                min_trace_width = conductive_head.get("traceWidth", DEFAULT_NOZZLE)
                nozzle_size     = conductive_head.get("nozzleSize", DEFAULT_NOZZLE)
                flow_rate       = conductive_head.get("flowRate", 0.05)
                layer_height    = conductive_head.get("layerHeight", 0.2)

                raw_segments, _, _ = extract_traces(
                    gbr_path, offset_x=global_offset_x, offset_y=global_offset_y,
                    min_trace_width=min_trace_width
                )
                pads, _, _ = extract_coords(gbr_path, offset_x=global_offset_x, offset_y=global_offset_y)

                # ── PRIME LEAD SCREW ──────────────────────────────────
                if enable_purge:
                    prime_lead_screw(g, work_z=copper_work_z, lift_z=copper_work_z + 5.5)

                if raw_segments:
                    print(f"  extracted {len(raw_segments)} trace segments")
                    layer_toolpaths = generate_shapely_toolpaths(raw_segments, min_trace_width, pads=pads,
                                                                 subtract_rect_pads=ink_traces_only,
                                                                 edge_inset=trace_edge_inset)
                    layer_toolpaths = order_paths_nearest(layer_toolpaths)
                    for path in layer_toolpaths:
                        if not path or len(path) < 2:
                            continue
                        current_e = points_to_gcode_path(
                            g, path, current_e, flow_rate, layer_height,
                            min_trace_width, enable_extrusion, use_arc_moves,
                            work_z=copper_work_z, pullpush=global_pullpush, pullpush_speed=global_pullpush_speed, lift_z=copper_hop_z
                        )
                        if enable_extrusion:
                            current_e -= retraction_distance
                            g.write(f"G1 E{current_e:.5f} F{RETRACT_FEED}")

                if pads and ink_traces_only:
                    print(f"  skipping {len(pads)} ink pads (pads drawn by solder paste head)")
                elif pads:
                    print(f"  extracted {len(pads)} pads")
                    for px, py, size, shape in pads:
                        if shape == 'C':
                            circles = generate_pad_spiral(px, py, size / 2, nozzle_size, use_arc_moves=use_arc_moves)
                            g.rapid(z=copper_hop_z)
                            g.rapid(point=(circles[0][0], circles[0][1]))
                            g.rapid(z=copper_work_z)
                            last_x, last_y = circles[0][0], circles[0][1]
                            for cx, cy, new_circle, arc_i, arc_j in circles:
                                if new_circle:
                                    g.move(e=-global_pullpush, f=global_pullpush_speed)
                                    g.rapid(z=copper_hop_z)
                                    g.rapid(point=(cx, cy))
                                    g.rapid(z=copper_work_z)
                                    g.move(e=global_pullpush, f=global_pullpush_speed)
                                    last_x, last_y = cx, cy
                                elif use_arc_moves and arc_i != 0:
                                    g.write(f"G2 X{cx:.4f} Y{cy:.4f} I{arc_i:.4f} J{arc_j:.4f}")
                                    last_x, last_y = cx, cy
                                else:
                                    current_e = move_with_extrusion(g, cx, cy, last_x, last_y, current_e, flow_rate, layer_height, min_trace_width, enable_extrusion)
                                    last_x, last_y = cx, cy
                            g.rapid(z=copper_hop_z)
                        else:
                            all_rects = generate_pad_raster(px, py, size, nozzle_size, shape=shape)
                            # one plunge + prime per pad: stay at work
                            # height between concentric rings
                            first_ring = True
                            for rect_segments in all_rects:
                                if not rect_segments:
                                    continue
                                if first_ring:
                                    g.move(e=-global_pullpush, f=global_pullpush_speed)
                                    g.rapid(z=copper_hop_z)
                                    g.rapid(point=(rect_segments[0][0], rect_segments[0][1]))
                                    g.rapid(z=copper_work_z)
                                    g.move(e=global_pullpush, f=global_pullpush_speed)
                                    last_x, last_y = rect_segments[0][0], rect_segments[0][1]
                                    first_ring = False
                                else:
                                    current_e = move_with_extrusion(g, rect_segments[0][0], rect_segments[0][1], last_x, last_y, current_e, flow_rate, layer_height, min_trace_width, enable_extrusion)
                                    last_x, last_y = rect_segments[0][0], rect_segments[0][1]
                                for sx, sy, ex, ey in rect_segments:
                                    current_e = move_with_extrusion(g, ex, ey, last_x, last_y, current_e, flow_rate, layer_height, min_trace_width, enable_extrusion)
                                    last_x, last_y = ex, ey
                # cure + camera sweep only after the FINAL copper pass;
                # with the second ink layer on, pass 2 prints wet-on-wet
                # and everything cures/sweeps once at the end
                if copper_passes_done >= total_copper_passes:
                    g.write(f"G1 E-{INK_FINAL_PULL_E} F{INK_FINAL_PULL_FEED} ; large pull out to stop ink extrusion")
                    if enable_camera_sweep:
                        if pads or raw_segments:
                            all_x = [p[0] for p in pads] + \
                                    [s[0][0] for s in raw_segments] + \
                                    [s[1][0] for s in raw_segments]
                            all_y = [p[1] for p in pads] + \
                                    [s[0][1] for s in raw_segments] + \
                                    [s[1][1] for s in raw_segments]
                            sweep_origin_x = min(all_x)
                            sweep_origin_y = min(all_y)
                            sweep_size_x   = max(all_x) - sweep_origin_x
                            sweep_size_y   = max(all_y) - sweep_origin_y
                        else:
                            sweep_origin_x = global_offset_x
                            sweep_origin_y = global_offset_y
                            sweep_size_x   = board_size_x
                            sweep_size_y   = board_size_y
                        # send the design to the vision system just before it inspects
                        if enable_gerber_transfer:
                            g.write("G0 Z20")
                            emit_gerber_transfer(g, files_to_process)
                        camera_sweep(g, safe_z=camera_work_z,
                                     board_size_x=sweep_size_x,
                                     board_size_y=sweep_size_y,
                                     layer_index=0,
                                     camera_head_tool=camera_tool_number,
                                     origin_x=sweep_origin_x,
                                     origin_y=sweep_origin_y)

                    g.rapid(x=PARK_X, y=PARK_Y, z=PARK_Z)  # park away from board for cure
                    if enable_heating:
                        cure_dry_temp    = conductive_head.get("cureDryTemp", 90)
                        cure_dry_seconds = conductive_head.get("cureDrySeconds", 300)
                        cure_temp        = conductive_head.get("cureTemp", 170)
                        cure_seconds     = conductive_head.get("cureSeconds", 900)
                        g.write(f"M190 S{cure_dry_temp}")
                        g.write("G4 S300 ; stage 1: dry")
                        g.write(f"M190 S{cure_temp}")
                        g.write("G4 S900 ; stage 2: cure")
                        g.write("M190 S0")
                        g.write(f"G4 S{COOLDOWN_WAIT_S} ; cool-down wait")


            elif layer_type == "paste":
                if not enable_paste:
                    print(f"  skipping {fname} (solder paste disabled)")
                    continue
                paste_head = get_head(configFile, "paste")
                pads, _, _   = extract_coords(gbr_path, offset_x=global_offset_x, offset_y=global_offset_y)
                if enable_tool_change:
                    g.write(f"T{paste_head.get('toolNumber', 1)} ; paste head")
                    nozzle_wipe(g, work_z=paste_work_z, lift_z=paste_hop_z)
                print(f"  extracted {len(pads)} paste pads")

                # ── PRIME LEAD SCREW ──────────────────────────────────
                if enable_purge:
                    prime_lead_screw(g, work_z=paste_work_z, extrude_amount=40, prime_cycles=10, lift_z=paste_work_z + 5.5)

                paste_dot_e     = configFile.get("pasteDotE", 20)
                paste_retract_e = configFile.get("pasteRetractE", 0)
                for px, py, size, shape in pads:
                    # dot dispense: plunge to the pad center, push a fixed
                    # extrusion amount, retract — no motion fill
                    g.rapid(z=paste_hop_z)
                    g.rapid(point=(px, py))
                    g.rapid(z=paste_work_z)
                    g.write(f"G1 E{paste_dot_e} F{paste_pullpush_speed} ; dispense paste dot")
                    g.write("G4 S0.5 ; delay")
                    g.write(f"G1 E-{paste_retract_e} F{paste_pullpush_speed} ; retract")
                    # small lateral wiggle to shear the paste string off the tip
                    g.rapid(point=(px + PASTE_WIGGLE, py))
                    g.rapid(point=(px, py))
                g.rapid(z=paste_hop_z)

            elif layer_type == "insulator":
                ins_nozzle_size  = insulator_head.get("nozzleSize", DEFAULT_NOZZLE)
                ins_flow_rate    = insulator_head.get("flowRate", 0.04)
                ins_layer_height = insulator_head.get("layerHeight", 0.2)
                ins_trace_width  = insulator_head.get("traceWidth", DEFAULT_NOZZLE)

                raw_segments, _, _ = extract_traces(
                    gbr_path, offset_x=global_offset_x, offset_y=global_offset_y,
                    min_trace_width=ins_trace_width
                )
                pads, _, _ = extract_coords(gbr_path, offset_x=global_offset_x, offset_y=global_offset_y)

                if enable_tool_change:
                    g.write(f"T{insulator_head.get('toolNumber', 4)} ; insulator head")

                if raw_segments:
                    print(f"  extracted {len(raw_segments)} insulator trace segments")
                    layer_toolpaths = generate_shapely_toolpaths(raw_segments, min_trace_width, pads=pads)
                    layer_toolpaths = order_paths_nearest(layer_toolpaths)
                    for path in layer_toolpaths:
                        if not path or len(path) < 2:
                            continue
                        current_e = points_to_gcode_path(
                            g, path, current_e, ins_flow_rate, ins_layer_height,
                            ins_trace_width, enable_extrusion, use_arc_moves,
                            work_z=insulator_work_z, pullpush=global_pullpush, pullpush_speed=global_pullpush_speed, lift_z=insulator_hop_z
                        )
                        if enable_extrusion:
                            current_e -= retraction_distance
                            g.write(f"G1 E{current_e:.5f} F{RETRACT_FEED}")

                if pads:
                    print(f"  extracted {len(pads)} insulator pads")
                    for px, py, size, shape in pads:
                        if shape == 'C':
                            circles = generate_pad_spiral(px, py, size / 2, ins_nozzle_size, use_arc_moves=use_arc_moves)
                            g.rapid(z=insulator_hop_z)
                            g.rapid(point=(circles[0][0], circles[0][1]))
                            g.rapid(z=insulator_work_z)
                            last_x, last_y = circles[0][0], circles[0][1]
                            for cx, cy, new_circle, arc_i, arc_j in circles:
                                if new_circle:
                                    g.move(e=-global_pullpush, f=global_pullpush_speed)
                                    g.rapid(z=insulator_hop_z)
                                    g.rapid(point=(cx, cy))
                                    g.rapid(z=insulator_work_z)
                                    g.move(e=global_pullpush, f=global_pullpush_speed)
                                    last_x, last_y = cx, cy
                                elif use_arc_moves and arc_i != 0:
                                    g.write(f"G2 X{cx:.4f} Y{cy:.4f} I{arc_i:.4f} J{arc_j:.4f}")
                                    last_x, last_y = cx, cy
                                else:
                                    current_e = move_with_extrusion(g, cx, cy, last_x, last_y, current_e, ins_flow_rate, ins_layer_height, ins_trace_width, enable_extrusion)
                                    last_x, last_y = cx, cy
                            g.rapid(z=insulator_hop_z)
                        else:
                            all_rects = generate_pad_raster(px, py, size, ins_nozzle_size, shape=shape)
                            # one plunge + prime per pad: stay at work
                            # height between concentric rings
                            first_ring = True
                            for rect_segments in all_rects:
                                if not rect_segments:
                                    continue
                                if first_ring:
                                    g.move(e=-global_pullpush, f=global_pullpush_speed)
                                    g.rapid(z=insulator_hop_z)
                                    g.rapid(point=(rect_segments[0][0], rect_segments[0][1]))
                                    g.rapid(z=insulator_work_z)
                                    g.move(e=global_pullpush, f=global_pullpush_speed)
                                    last_x, last_y = rect_segments[0][0], rect_segments[0][1]
                                    first_ring = False
                                else:
                                    current_e = move_with_extrusion(g, rect_segments[0][0], rect_segments[0][1], last_x, last_y, current_e, ins_flow_rate, ins_layer_height, ins_trace_width, enable_extrusion)
                                    last_x, last_y = rect_segments[0][0], rect_segments[0][1]
                                for sx, sy, ex, ey in rect_segments:
                                    current_e = move_with_extrusion(g, ex, ey, last_x, last_y, current_e, ins_flow_rate, ins_layer_height, ins_trace_width, enable_extrusion)
                                    last_x, last_y = ex, ey                
                g.rapid(x=PARK_X, y=PARK_Y, z=PARK_Z)  # park away from board for cure
                if enable_heating:
                    ins_cure_temp    = insulator_head.get("cureTemp", 135)
                    ins_cure_seconds = insulator_head.get("cureSeconds", 600)
                    g.write(f"M190 S{ins_cure_temp}")
                    g.sleep(ins_cure_seconds)
                    g.write("M190 S0")
                    g.write(f"G4 S{COOLDOWN_WAIT_S} ; cool-down wait")

                if enable_camera_sweep:
                    if pads or raw_segments:
                        all_x = [p[0] for p in pads] + \
                                [s[0][0] for s in raw_segments] + \
                                [s[1][0] for s in raw_segments]
                        all_y = [p[1] for p in pads] + \
                                [s[0][1] for s in raw_segments] + \
                                [s[1][1] for s in raw_segments]
                        sweep_origin_x = min(all_x)
                        sweep_origin_y = min(all_y)
                        sweep_size_x   = max(all_x) - sweep_origin_x
                        sweep_size_y   = max(all_y) - sweep_origin_y
                        camera_sweep(g, safe_z=camera_work_z,
                                     board_size_x=sweep_size_x,
                                     board_size_y=sweep_size_y,
                                     layer_index=1,
                                     camera_head_tool=camera_tool_number,
                                     origin_x=sweep_origin_x,
                                     origin_y=sweep_origin_y)

            elif layer_type == "copper_crossover":
                if not enable_crossover:
                    print(f"  skipping {fname} (crossover disabled)")
                    continue
                min_trace_width = conductive_head.get("traceWidth", DEFAULT_NOZZLE)
                nozzle_size     = conductive_head.get("nozzleSize", DEFAULT_NOZZLE)
                flow_rate       = conductive_head.get("flowRate", 0.05)
                layer_height    = conductive_head.get("layerHeight", 0.2)

                raw_segments, _, _ = extract_traces(
                    gbr_path, offset_x=global_offset_x, offset_y=global_offset_y,
                    min_trace_width=min_trace_width
                )
                pads, _, _ = extract_coords(gbr_path, offset_x=global_offset_x, offset_y=global_offset_y)

                if enable_tool_change:
                    g.write(f"T{conductive_head.get('toolNumber', 0)} ; conductive head (crossover layer)")

                if raw_segments:
                    print(f"  extracted {len(raw_segments)} crossover trace segments")
                    layer_toolpaths = generate_shapely_toolpaths(raw_segments, min_trace_width, pads=pads,
                                                                 edge_inset=trace_edge_inset)
                    layer_toolpaths = order_paths_nearest(layer_toolpaths)
                    for path in layer_toolpaths:
                        if not path or len(path) < 2:
                            continue
                        current_e = points_to_gcode_path(
                            g, path, current_e, flow_rate, layer_height,
                            min_trace_width, enable_extrusion, use_arc_moves,
                            work_z=crossover_work_z, pullpush=global_pullpush, pullpush_speed=global_pullpush_speed, lift_z=crossover_hop_z
                        )
                        if enable_extrusion:
                            current_e -= retraction_distance
                            g.write(f"G1 E{current_e:.5f} F{RETRACT_FEED}")

                if pads:
                    print(f"  extracted {len(pads)} crossover pads")
                    for px, py, size, shape in pads:
                        if shape == 'C':
                            circles = generate_pad_spiral(px, py, size / 2, nozzle_size)
                            g.rapid(z=crossover_hop_z)
                            g.rapid(point=(circles[0][0], circles[0][1]))
                            g.rapid(z=crossover_work_z)
                            last_x, last_y = circles[0][0], circles[0][1]
                            for cx, cy, new_circle, arc_i, arc_j in circles:
                                if new_circle:
                                    g.move(e=-global_pullpush, f=global_pullpush_speed)
                                    g.rapid(z=crossover_hop_z)
                                    g.rapid(point=(cx, cy))
                                    g.rapid(z=crossover_work_z)
                                    g.move(e=global_pullpush, f=global_pullpush_speed)
                                    last_x, last_y = cx, cy
                                else:
                                    current_e = move_with_extrusion(g, cx, cy, last_x, last_y, current_e, flow_rate, layer_height, min_trace_width, enable_extrusion)
                                    last_x, last_y = cx, cy
                            g.rapid(z=crossover_hop_z)
                        else:
                            all_rects = generate_pad_raster(px, py, size, nozzle_size, shape=shape)
                            # one plunge + prime per pad: stay at work
                            # height between concentric rings
                            first_ring = True
                            for rect_segments in all_rects:
                                if not rect_segments:
                                    continue
                                if first_ring:
                                    g.move(e=-global_pullpush, f=global_pullpush_speed)
                                    g.rapid(z=crossover_hop_z)
                                    g.rapid(point=(rect_segments[0][0], rect_segments[0][1]))
                                    g.rapid(z=crossover_work_z)
                                    g.move(e=global_pullpush, f=global_pullpush_speed)
                                    last_x, last_y = rect_segments[0][0], rect_segments[0][1]
                                    first_ring = False
                                else:
                                    current_e = move_with_extrusion(g, rect_segments[0][0], rect_segments[0][1], last_x, last_y, current_e, flow_rate, layer_height, min_trace_width, enable_extrusion)
                                    last_x, last_y = rect_segments[0][0], rect_segments[0][1]
                                for sx, sy, ex, ey in rect_segments:
                                    current_e = move_with_extrusion(g, ex, ey, last_x, last_y, current_e, flow_rate, layer_height, min_trace_width, enable_extrusion)
                                    last_x, last_y = ex, ey
                g.rapid(x=PARK_X, y=PARK_Y, z=PARK_Z)  # park away from board for cure
                if enable_heating:
                    g.write(f"M190 S{conductive_head.get('cureDryTemp', 90)}")
                    g.sleep(conductive_head.get("cureDrySeconds", 300))
                    g.write(f"M190 S{conductive_head.get('cureTemp', 170)}")
                    g.sleep(conductive_head.get("cureSeconds", 900))
                    g.write("M190 S0")
                    g.write(f"G4 S{COOLDOWN_WAIT_S} ; cool-down wait")

                if enable_camera_sweep:
                    if pads or raw_segments:
                        all_x = [p[0] for p in pads] + \
                                [s[0][0] for s in raw_segments] + \
                                [s[1][0] for s in raw_segments]
                        all_y = [p[1] for p in pads] + \
                                [s[0][1] for s in raw_segments] + \
                                [s[1][1] for s in raw_segments]
                        sweep_origin_x = min(all_x)
                        sweep_origin_y = min(all_y)
                        sweep_size_x   = max(all_x) - sweep_origin_x
                        sweep_size_y   = max(all_y) - sweep_origin_y
                        camera_sweep(g, safe_z=camera_work_z,
                                     board_size_x=sweep_size_x,
                                     board_size_y=sweep_size_y,
                                     layer_index=2,
                                     camera_head_tool=camera_tool_number,
                                     origin_x=sweep_origin_x,
                                     origin_y=sweep_origin_y)

        g.write("\n; --- END PRINT ---")
        g.write("G28 X0 Y0 ; home X and Y")
        g.write("M84 ; disable motors")

    # count lines and prepend total to file
    with open(output_file, 'r') as f:
        lines = f.readlines()
    total_lines = len(lines)
    with open(output_file, 'w') as f:
        f.write(f"; TOTAL_LINES:{total_lines}\n")
        f.writelines(lines)

    print(f"Total G-code lines: {total_lines}")
    print(f"\nSuccess! G-code saved to {output_file}")


if __name__ == "__main__":
    run(enable_extrusion=True, use_arc_moves=True)