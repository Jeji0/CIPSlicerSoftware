from __future__ import annotations
import os
import json
import re
import math
import zipfile

from pygerber.gerber.api import GerberFile, GerberJobFile
from gscrib import GCodeBuilder

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
    """Automatically select the correct head based on layer type.
    copper → conductive head
    paste  → solder paste head
    between copper layers → insulator head"""
    if layer_type == "copper":
        return get_head(configFile, "conductive")
    #if layer_type == "paste":
        #return get_head(configFile, "paste")
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
    #if "solderpaste" in f:  return "paste"
    if "soldermask" in f:   return "mask"
    if "legend" in f:       return "silkscreen"
    if "profile" in f:      return "edge"
    return "unknown"

def get_layer_type_from_filename(filename: str) -> str:
    """Detect layer type from Gerber filename for boards without a standard .gbrjob.
    Handles KiCad, Fusion, Altium, and Eagle naming conventions."""
    f = filename.lower()
    if any(x in f for x in ["copper_top", "f_cu", "top_copper", "gtl"]):
        return "copper_top"
    if any(x in f for x in ["copper_bottom", "b_cu", "bottom_copper", "gbl"]):
        return "copper_bottom"
    #if any(x in f for x in ["solderpaste_top", "f_paste", "top_paste", "gtp"]):
        #return "paste_top"
    #if any(x in f for x in ["solderpaste_bottom", "b_paste", "bottom_paste", "gbp"]):
        #return "paste_bottom"
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
    # detect units — Fusion exports in inches, KiCad in mm
    # %MOIN*% = inches, %MOMM*% = mm
    scale = 25.4 if "%MOIN*%" in source else 1.0
    # detect coordinate format — FSLAX34 = divide by 10^4, FSLAX56 = divide by 10^6
    fmt_match = re.search(r'%FSLA[XY](\d)(\d)', source)
    if fmt_match:
        decimal_places = int(fmt_match.group(2))
        divisor = 10 ** decimal_places
    else:
        divisor = 1_000_000  # default KiCad format


    # parse aperture definitions — handle C, R, and RoundRect
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
                # store as negative to signal it's a RoundRect with w/h
                # encode width in size, height in shape string
                size  = width
                shape = f'RR:{height}'  # e.g. 'RR:0.2'
            except:
                size  = 0.6
                shape = 'C'
        else:
            size  = float(params[0]) if params else 0.2
            shape = 'C'
        
        aperture_sizes[apt_id]  = size
        aperture_shapes[apt_id] = shape

    print(aperture_sizes)

    # extract pad positions with their aperture size and shape
    raw = []
    current_aperture = None

    for line in source.split('\n'):
        apt_match = re.match(r'D(\d+)\*', line.strip())
        if apt_match and int(apt_match.group(1)) >= 10:
            current_aperture = apt_match.group(1)
        d03_match = re.match(r'X(-?\d+)Y(-?\d+)D03', line.strip())
        if d03_match:
            x     = int(d03_match.group(1)) / divisor * scale
            y     = int(d03_match.group(2)) / divisor * scale
            size  = aperture_sizes.get(current_aperture, 0.2)
            shape = aperture_shapes.get(current_aperture, 'C')
            raw.append((x, y, size, shape))

    if not raw:
        return [], 0, 0

    min_x = offset_x if offset_x is not None else min(c[0] for c in raw)
    min_y = offset_y if offset_y is not None else min(c[1] for c in raw)
    return [(x - min_x, y - min_y, size, shape) for x, y, size, shape in raw], min_x, min_y

def approximate_arc(x1, y1, x2, y2, i, j, clockwise, segments=16):
    """Approximate a Gerber arc as linear segments.
    i, j are center offsets from start point.
    Returns list of (x, y) points along the arc."""
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

    # parse aperture sizes for traces
    aperture_sizes = {}
    for match in re.finditer(r'%ADD(\d+)([A-Za-z]+),([^*]+)\*%', source):
        apt_id   = match.group(1)
        apt_type = match.group(2)
        params   = match.group(3).split('X')
        if apt_type in ('C', 'R'):
            aperture_sizes[apt_id] = float(params[0]) * scale
        else:
            aperture_sizes[apt_id] = float(params[0]) * scale if params else 0.225
    

    print(aperture_sizes)

    raw_segments = []
    current_x = 0.0
    current_y = 0.0
    current_aperture = None
    arc_mode = None  # 'G02' clockwise, 'G03' counterclockwise

    for line in source.split('\n'):
        line = line.strip()
        
        # track arc mode (handles both same-line and separate-line G02/G03)
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

        # arc move: G02/G03 may be on same line as coordinates (KiCad format)
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

        # arc with only I (no J) or only J (no I)
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

        # linear move
        coord_match = re.match(r'X(-?\d+)Y(-?\d+)(D0[123])', line)
        if coord_match:
            x   = int(coord_match.group(1)) / divisor * scale
            y   = int(coord_match.group(2)) / divisor * scale
            cmd = coord_match.group(3)
            if cmd == 'D02':
                current_x = x
                current_y = y
            elif cmd == 'D01':
                raw_segments.append(((current_x, current_y), (x, y), trace_width))
                current_x = x
                current_y = y
            elif cmd == 'D03':
                current_x = x
                current_y = y

    if not raw_segments:
        return raw_segments, 0, 0

    all_x = [s[0][0] for s in raw_segments] + [s[1][0] for s in raw_segments]
    all_y = [s[0][1] for s in raw_segments] + [s[1][1] for s in raw_segments]
    min_x = offset_x if offset_x is not None else min(all_x)
    min_y = offset_y if offset_y is not None else min(all_y)

    normalized = [((sx - min_x, sy - min_y), (ex - min_x, ey - min_y), tw)
                  for (sx, sy), (ex, ey), tw in raw_segments]
    return normalized, min_x, min_y


def segments_intersect(p1, p2, p3, p4):
    """Check if segment p1-p2 intersects p3-p4. Returns intersection point or None.
    Uses 0.01 threshold to avoid detecting shared endpoints as crossings."""
    x1,y1 = p1; x2,y2 = p2; x3,y3 = p3; x4,y4 = p4
    denom = (x1-x2)*(y3-y4) - (y1-y2)*(x3-x4)
    if abs(denom) < 1e-10:
        return None
    t = ((x1-x3)*(y3-y4) - (y1-y3)*(x3-x4)) / denom
    u = -((x1-x2)*(y1-y3) - (y1-y2)*(x1-x3)) / denom
    if 0.01 < t < 0.99 and 0.01 < u < 0.99:
        return (x1 + t*(x2-x1), y1 + t*(y2-y1))
    return None

def find_trace_intersections(traces):
    crossings = []
    for i in range(len(traces)):
        for j in range(i+1, len(traces)):
            pt = segments_intersect(traces[i][0], traces[i][1], traces[j][0], traces[j][1])
            if pt:
                crossings.append((pt[0], pt[1], i, j))
    return crossings

#def chain_segments(traces, tolerance=0.001):
    # """Chain trace segments that share endpoints into continuous paths.
    # Returns list of (points, trace_width) tuples."""
    # used = [False] * len(traces)
    # paths = []

    # for start in range(len(traces)):
    #     if used[start]:
    #         continue
    #     used[start] = True
    #     s, e, tw = traces[start]
    #     path = [s, e]
    #     path_width = tw

    #     changed = True
    #     while changed:
    #         changed = False
    #         for k in range(len(traces)):
    #             if used[k]:
    #                 continue
    #             s, e, tw = traces[k]
    #             if math.dist(path[-1], s) < tolerance:
    #                 path.append(e)
    #                 used[k] = True
    #                 changed = True
    #             elif math.dist(path[-1], e) < tolerance:
    #                 path.append(s)
    #                 used[k] = True
    #                 changed = True
    #     paths.append((path, path_width))
    # return paths

def calculate_fill_passes(trace_width_mm: float, nozzle_size_mm: float) -> int:
    return math.ceil(trace_width_mm / nozzle_size_mm)


def generate_fill_offsets(x: float, y: float, next_x: float, next_y: float, nozzle_size: float, passes: int, trace_width: float) -> list[tuple[float, float]]:
    dx = next_x - x
    dy = next_y - y
    length = math.sqrt(dx**2 + dy**2)
    if length == 0:
        return [(x, y)]
    perp_x = -dy / length
    perp_y = dx / length
    if passes == 1:
        spacing = 0
    else:
        overlap = (passes * nozzle_size - trace_width) / (passes - 1)
        spacing = nozzle_size - overlap
    points = []
    for i in range(passes):
        offset = (i - (passes - 1) / 2) * spacing
        ox = max(0, x + perp_x * offset)
        oy = max(0, y + perp_y * offset)
        points.append((ox, oy))
    return points

def generate_pad_spiral(cx: float, cy: float, radius: float, nozzle_size: float) -> list[tuple[float, float, bool]]:
    """Fill a circular pad with concentric circles from center outward.

    Deprecated for G-code output: this returns many points that become segmented G1
    moves. It is kept here as a fallback/reference, but circular pads are now
    emitted with G2/G3 arcs by deposit_circular_pad_arcs().
    """
    points = []
    step       = nozzle_size * 0.8
    angle_step = 0.15

    r = step
    while r <= radius:
        steps = int(2 * math.pi / angle_step) + 1
        for i in range(steps + 1):
            a = i * angle_step
            x = cx + r * math.cos(a)
            y = cy + r * math.sin(a)
            points.append((x, y, i == 0))
        r += step

    return points


def gcode_float(value: float, decimals: int = 6) -> str:
    """Format a G-code number without trailing zero clutter."""
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    if text == "-0":
        return "0"
    return text if text else "0"


def generate_pad_arc_radii(radius: float, nozzle_size: float) -> list[float]:
    """Return the same center-out concentric pad radii formerly used by generate_pad_spiral()."""
    if radius <= 0:
        return []
    if nozzle_size <= 0:
        raise ValueError("nozzle_size must be greater than 0")

    step = nozzle_size * 0.8
    radii = []

    r = step
    while r <= radius:
        radii.append(r)
        r += step

    # If the pad is bigger than the nozzle but smaller than the first normal ring,
    # still emit one arc at the pad radius instead of silently producing no pad path.
    if not radii:
        radii.append(radius)

    return radii


def get_pad_arc_clockwise(configFile: dict) -> bool:
    """Select G2/G3 direction for circular pad rings.

    Default is G3/CCW because the old point-based circle generator walked around
    each circle with increasing angle, which is counterclockwise.
    Optional config values:
      "padArcDirection": "G2", "CW", "CLOCKWISE", "G3", "CCW", or "COUNTERCLOCKWISE"
      "padArcClockwise": true/false  # backwards/simple boolean option
    """
    direction = str(configFile.get("padArcDirection", "")).strip().upper()
    if direction in {"G2", "CW", "CLOCKWISE"}:
        return True
    if direction in {"G3", "CCW", "COUNTERCLOCKWISE"}:
        return False

    value = configFile.get("padArcClockwise", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "g2", "cw", "clockwise"}
    return bool(value)


def deposit_circular_pad_arcs(
    g,
    cx: float,
    cy: float,
    radius: float,
    nozzle_size: float,
    work_z: float,
    safe_z: float,
    clockwise: bool = False,
) -> None:
    """Deposit a circular pad as concentric full-circle G2/G3 arcs.

    A full circle starts and ends at the same point. I/J are relative center
    offsets from the arc start point, matching common G-code arc syntax:

        start = (cx + radius, cy)
        center offset = I=-radius, J=0

    The result is one G2/G3 command per ring instead of many short G1 chords.
    """
    arc_code = "G2" if clockwise else "G3"

    for r in generate_pad_arc_radii(radius, nozzle_size):
        start_x = cx + r
        start_y = cy

        g.rapid(z=safe_z)
        g.rapid(point=(start_x, start_y))
        g.rapid(z=work_z)

        g.write(
            f"{arc_code} "
            f"X{gcode_float(start_x)} "
            f"Y{gcode_float(start_y)} "
            f"I{gcode_float(-r)} "
            f"J0"
        )

    g.rapid(z=safe_z)


def get_trace_cap_mode(configFile: dict) -> str:
    """Return how semicircular trace caps should be added to trace endpoints.

    Modes:
      terminal  - cap only open trace ends, where a trace endpoint is not shared
                  by another trace segment. This is the default and is usually
                  the closest match to Gerber circular-aperture stroke ends.
      all       - cap every unique segment endpoint, including corners/junctions.
                  For joined points, the widest segment's direction is used.
      segment   - cap both ends of every segment without de-duplication. Mostly
                  useful for testing and usually heavier than needed.
      none      - disable trace caps.

    Backwards/simple option:
      "traceCapsEnabled": false  -> same as traceCapMode "none"
    """
    if "traceCapMode" not in configFile:
        enabled = configFile.get("traceCapsEnabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in {"0", "false", "no", "n", "off", "none"}
        return "terminal" if bool(enabled) else "none"

    mode = str(configFile.get("traceCapMode", "terminal")).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "terminal",
        "on": "terminal",
        "true": "terminal",
        "yes": "terminal",
        "end": "terminal",
        "ends": "terminal",
        "open_ends": "terminal",
        "terminal_endpoints": "terminal",
        "terminals": "terminal",
        "unique": "all",
        "all_unique": "all",
        "all_endpoints": "all",
        "every_endpoint": "all",
        "per_segment": "segment",
        "segments": "segment",
        "segment_endpoints": "segment",
        "off": "none",
        "false": "none",
        "no": "none",
        "0": "none",
        "disabled": "none",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"terminal", "all", "segment", "none"}:
        print(f"WARNING: unknown traceCapMode {mode!r}; using 'terminal'")
        return "terminal"
    return mode


def _point_key(x: float, y: float, tolerance: float) -> tuple[int, int]:
    """Quantize a point so nearly identical Gerber endpoints de-duplicate."""
    tolerance = max(float(tolerance), 1e-9)
    return (round(x / tolerance), round(y / tolerance))


def collect_trace_caps(
    traces: list[tuple[tuple[float, float], tuple[float, float], float]],
    mode: str = "terminal",
    tolerance: float = 0.001,
) -> list[tuple[float, float, float, float, float]]:
    """Collect trace-end cap definitions.

    Returns a list of:
        (cap_x, cap_y, interior_x, interior_y, trace_width)

    cap_x/cap_y are the Gerber trace endpoint. interior_x/interior_y is a point
    along the trace body, used to determine which side of the endpoint should
    receive the semicircular cap. This is what lets the cap be a half circle on
    the outside end of the trace instead of a full circular pad.
    """
    mode = str(mode).strip().lower()
    if mode == "none" or not traces:
        return []

    if mode == "segment":
        caps = []
        for (sx, sy), (ex, ey), width in traces:
            caps.append((sx, sy, ex, ey, width))
            caps.append((ex, ey, sx, sy, width))
        return caps

    grouped: dict[tuple[int, int], list[tuple[float, float, float, float, float]]] = {}
    for (sx, sy), (ex, ey), width in traces:
        grouped.setdefault(_point_key(sx, sy, tolerance), []).append((sx, sy, ex, ey, width))
        grouped.setdefault(_point_key(ex, ey, tolerance), []).append((ex, ey, sx, sy, width))

    caps = []
    for entries in grouped.values():
        if mode == "terminal" and len(entries) != 1:
            continue

        # For "all" mode, use the widest segment at that endpoint. This avoids
        # piling several caps on top of each other at a junction, while still
        # giving a deterministic outside direction.
        cap_x, cap_y, interior_x, interior_y, width = max(entries, key=lambda item: item[4])
        if mode == "all":
            count = len(entries)
            cap_x = sum(item[0] for item in entries) / count
            cap_y = sum(item[1] for item in entries) / count
        caps.append((cap_x, cap_y, interior_x, interior_y, width))
    return caps


def generate_trace_cap_centerline_radii(trace_width: float, nozzle_size: float, extra_radius: float = 0.0) -> list[float]:
    """Return centerline radii for semicircular trace caps.

    The previous circular-cap approach used trace_width / 2 as a pad radius,
    which creates a full circular pad. For a filled trace made from parallel
    offset line passes, the semicircular cap should instead connect the same
    positive/negative offset passes at each end of the trace.

    Example: if the trace body is filled with offsets -0.18, 0, +0.18, this
    returns [0.18], so the cap is one semicircle from +0.18 to -0.18. Wider
    traces get multiple concentric semicircles, matching the existing fill-pass
    spacing.
    """
    if trace_width <= 0 or nozzle_size <= 0:
        return []

    passes = calculate_fill_passes(trace_width, nozzle_size)
    if passes <= 1:
        # A single nozzle-centerline trace already has a round physical end from
        # the nozzle/ink itself, so there is no separate centerline semicircle to add.
        return []

    overlap = (passes * nozzle_size - trace_width) / (passes - 1)
    spacing = nozzle_size - overlap

    radii = sorted({
        round(abs((i - (passes - 1) / 2) * spacing) + float(extra_radius), 9)
        for i in range(passes)
        if abs((i - (passes - 1) / 2) * spacing) > 1e-9
    })
    return [r for r in radii if r > 0]


def deposit_trace_cap(
    g,
    cx: float,
    cy: float,
    interior_x: float,
    interior_y: float,
    trace_width: float,
    nozzle_size: float,
    work_z: float,
    safe_z: float,
    clockwise: bool = False,  # kept for call compatibility; cap side determines G2/G3 choice
    extra_radius: float = 0.0,
) -> None:
    """Deposit a semicircular cap at one trace endpoint.

    The cap is centered on the Gerber endpoint and lies only on the outside end
    of the trace. It uses the same centerline offset spacing as the trace body,
    so it meets the offset trace passes instead of creating a full circular pad.
    """
    dx = interior_x - cx
    dy = interior_y - cy
    length = math.sqrt(dx * dx + dy * dy)
    if length <= 1e-9:
        return

    # Unit vector into the trace body and its left-hand perpendicular.
    ux = dx / length
    uy = dy / length
    perp_x = -uy
    perp_y = ux

    for r in generate_trace_cap_centerline_radii(trace_width, nozzle_size, extra_radius):
        start_x = cx + perp_x * r
        start_y = cy + perp_y * r
        end_x   = cx - perp_x * r
        end_y   = cy - perp_y * r

        # From +perp to -perp, G3 goes around the outside of the endpoint because
        # the interior direction points into the trace body.
        g.rapid(z=safe_z)
        g.rapid(point=(start_x, start_y))
        g.rapid(z=work_z)
        g.write(
            f"G3 "
            f"X{gcode_float(end_x)} "
            f"Y{gcode_float(end_y)} "
            f"I{gcode_float(cx - start_x)} "
            f"J{gcode_float(cy - start_y)}"
        )

    g.rapid(z=safe_z)


def generate_pad_raster(cx, cy, size, nozzle_size, shape='C'):
    """Fill rectangular pad with a single continuous rectangular spiral from outside inward."""
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
            points.append((cx + w, cy + h))   # top-right
            points.append((cx + w, cy - h))   # bottom-right
            points.append((cx - w, cy - h))   # bottom-left
            points.append((cx - w, cy + next_h))
            points.append((cx - next_w, cy + next_h))
        else:
            # h=0, only horizontal line remains
            points.append((cx + w, cy))
            points.append((cx + next_w, cy))
            break

    segments = []
    for i in range(len(points) - 1):
        segments.append((points[i][0], points[i][1], points[i+1][0], points[i+1][1]))
    return segments

def camera_sweep(g, safe_z: float, board_size_x: float = 0, board_size_y: float = 0, layer_index: int = 0) -> bool:
    """Camera sweep after each ink + cure sequence.
    Moves to sweep position and triggers CV system to check for shorts/coverage.
    Returns True if pass, False if fail — False stops the print.
    PLACEHOLDER - sweep pattern and CV communication to be confirmed with camera team."""
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


def deposit_insulator(g, coords: list, work_z: float, safe_z: float, nozzle_size: float, configFile: dict) -> None:
    """Deposit insulator layer (ACI SI3104) over all pad positions.
    Insulator is on a separate head — head offset from config is applied.
    Cure process: heat to 135C, hold for 5-15 minutes, cool down."""
    if not coords:
        print("  no coords for insulator layer, skipping")
        return

    # apply head offset for insulator head — confirm real offset with hardware team
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

    # M190 blocks until bed reaches temperature before dwell starts
    print(f"  insulator cure: 135C for {insulator_cure_seconds}s")
    g.write(f"M190 S{int(insulator_head.get('cureTemp', 135))}")
    g.sleep(insulator_cure_seconds)
    g.write("M140 S0")  # turn off heater after cure


def run():
    """Main entry point — loads config, parses Gerber files, generates G-code."""

    # load machine and print settings from config.json
    with open(os.path.join(BASE_DIR, "config.json"), "r") as f:
        configFile = json.load(f)

    validate_config(configFile)

    # load head profiles
    conductive_head = get_head(configFile, "conductive")


    # Ender 3 default steps/mm — confirm if machine has been recalibrated
    steps_per_mm_x   = configFile.get("steps_per_mm_x", 80)
    steps_per_mm_y   = configFile.get("steps_per_mm_y", 80)
    steps_per_mm_z   = configFile.get("steps_per_mm_z", 400)

    # derive output .gcode path from the zip path in config
    # gerberFile path is relative to project root (one level up from src/)
    gerber_zip_path = configFile.get("gerberFile", "TestFiles/test-gbr.zip")
    project_root    = os.path.dirname(BASE_DIR)
    gerber_zip_full = os.path.join(project_root, gerber_zip_path)
    gerber_dir      = os.path.join(project_root, os.path.dirname(gerber_zip_path))
    gerber_name     = os.path.splitext(os.path.basename(gerber_zip_path))[0]
    output_file     = os.path.join(gerber_dir, gerber_name + ".gcode")
    extract_dir = os.path.join(gerber_dir, gerber_name)
    os.makedirs(extract_dir, exist_ok=True)

    # unzip Gerber files before loading the job file
    if gerber_zip_path.endswith(".zip") and os.path.exists(gerber_zip_full):
        with zipfile.ZipFile(gerber_zip_full, "r") as z:
            z.extractall(extract_dir)
        print(f"Extracted {gerber_zip_path} to {extract_dir}")
    else:
        print(f"Skipping extraction — {gerber_zip_path} already extracted or not a zip")

    # load the .gbrjob file
    gerber_job_file = configFile.get("gerberJobFile", "TestFiles/test-job.gbrjob")
    gerber_job_path = os.path.join(project_root, gerber_job_file)

    # try to parse as standard KiCad job file first
    # fall back to manual parsing for Fusion/Altium formats
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
        # fall back to manual job file parsing + filename detection
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

        # scan extracted directory for .gbr files
        for fname in sorted(os.listdir(extract_dir)):
            if not fname.endswith(".gbr"):
                continue
            layer_type_full = get_layer_type_from_filename(fname)
            if layer_type_full == "unknown":
                print(f"  skipping {fname} (unknown layer type)")
                continue
            # map to simple layer type
            if "copper" in layer_type_full:
                layer_type = "copper"
            elif "paste" in layer_type_full:
                layer_type = "paste"
            else:
                layer_type = layer_type_full
            is_bottom = "bottom" in layer_type_full
            gbr_path  = os.path.join(extract_dir, fname)
            files_to_process.append((gbr_path, layer_type, is_bottom))
            print(f"  found {fname} → {layer_type_full}")

    print(f"Board: {board_size_x}x{board_size_y}mm, {board_layers} layers, copper thickness: {copper_thickness}mm")

    # auto set layer mode based on board layer count if not set in config
    if "layerMode" not in configFile:
        layer_mode = "multi" if board_layers > 1 else "single"
        print(f"Auto layer mode: {layer_mode}")

    # write G-code using gscrib
    with GCodeBuilder(output=output_file) as g:

        # machine setup
        g.set_bounds("axes", min=(0, 0, 0), max=(configFile["maxBedSize"][0], configFile["maxBedSize"][1], 50))
        g.set_axis(point=(0, 0, 0))          # set current position as origin
        g.set_length_units("millimeters")
        g.set_time_units("seconds")
        g.set_distance_mode("absolute")      # all moves are absolute, not relative
        g.set_feed_rate(configFile.get("printSpeed", 60) * 10)  # convert mm/s to mm/min

        # home all axes before starting
        g.auto_home()

        # lift to safe height and start tool
        g.rapid(z=5)
        g.tool_on("clockwise", 1000)
        g.sleep(1)

        # single mode = top copper only, multi mode = all copper layers
        layer_mode  = configFile.get("layerMode", "single")
        layer_index = 0
        print(f"Layer mode: {layer_mode}")

        for gbr_path, layer_type, is_bottom in files_to_process:
            fname = os.path.basename(gbr_path)

            # skip non-printable layers
            if layer_type not in ["copper", "paste"]:
                print(f"  skipping {fname} ({layer_type})")
                continue

            # in single mode skip bottom layers
            if layer_mode == "single" and is_bottom:
                print(f"  skipping {fname} (single layer mode)")
                continue

            # find global minimum across ALL Gerber commands (D01, D02, D03)
            gerber_file_raw = GerberFile.from_file(gbr_path)
            all_matches = re.findall(r'X(-?\d+)Y(-?\d+)D0[123]', gerber_file_raw.source_code)
            if not all_matches:
                print(f"  no coordinates found in {fname}, skipping")
                continue

            # detect units
            scale = 25.4 if "%MOIN*%" in gerber_file_raw.source_code else 1.0
            # detect coordinate format
            fmt_match = re.search(r'%FSLA[XY](\d)(\d)', gerber_file_raw.source_code)
            if fmt_match:
                decimal_places = int(fmt_match.group(2))
                divisor = 10 ** decimal_places
            else:
                divisor = 1_000_000
            all_raw_x    = [int(x) / divisor * scale for x, y in all_matches]
            all_raw_y    = [int(y) / divisor * scale for x, y in all_matches]
            global_min_x = min(all_raw_x) - 1.0
            global_min_y = min(all_raw_y) - 2.0

            coords, _, _ = extract_coords(gbr_path, offset_x=global_min_x, offset_y=global_min_y)
            for i, (x, y, s, sh) in enumerate(coords[:]):
                print(f"  pad[{i}]: ({x:.3f},{y:.3f}) size={s:.4f} shape={sh}")
            _nozzle_size = get_head_for_layer(configFile, layer_type).get("nozzleSize", 0.225)
            traces, _, _ = extract_traces(gbr_path, offset_x=global_min_x, offset_y=global_min_y, min_trace_width=_nozzle_size)
            traces = [(s, e, tw) for s, e, tw in traces]
        

            if not coords:
                print(f"  no pads found in {fname}, skipping")
                continue

            out_of_bounds = [(x, y) for x, y, s, sh in coords if x > board_size_x or y > board_size_y]
            if out_of_bounds:
                print(f"WARNING: {len(out_of_bounds)} coords exceed board size — skipping them")
                coords = [(x, y, s, sh) for x, y, s, sh in coords if x <= board_size_x and y <= board_size_y]

            # filter out oversized pads (copper pours, fill zones)
            # coords = [(x, y, s, sh) for x, y, s, sh in coords if s <= 10.0]

            # mirror bottom layer coordinates on X axis
            if is_bottom:
                board_max_x = max(x for x, y, s, sh in coords)
                coords  = [(board_max_x - x, y, s, sh) for x, y, s, sh in coords]
                traces  = [((board_max_x - sx, sy), (board_max_x - ex, ey), tw) for (sx, sy), (ex, ey), tw in traces]

            layer_height           = configFile.get("conductiveLayerHeight", 0.2)
            insulator_layer_height = configFile.get("insulatorLayerHeight", 0.2)
            board_thickness     = configFile.get("boardThickness", 0.0)
            print_height_offset = configFile.get("printHeightOffset", 0.5)
            work_z = board_thickness + print_height_offset + (layer_index * layer_height)
            safe_z = work_z + 5
            active_head      = get_head_for_layer(configFile, layer_type)
            tool_number      = get_tool_number(active_head)
            g.write(f"T{tool_number}")
            print(f"  tool change: T{tool_number} ({active_head.get('id', 'unknown')})")
            cure_dry_seconds = active_head.get("cureDrySeconds", 300)
            cure_seconds     = active_head.get("cureSeconds", 900)
            nozzle_size      = active_head.get("nozzleSize", 0.225)
            trace_width      = active_head.get("traceWidth", 0.225)
            fill_passes      = calculate_fill_passes(trace_width, nozzle_size)
            pad_arc_clockwise = get_pad_arc_clockwise(configFile)
            pad_arc_code      = "G2" if pad_arc_clockwise else "G3"
            trace_cap_mode    = get_trace_cap_mode(configFile)
            trace_cap_tolerance = float(configFile.get("traceCapTolerance", 0.001))
            trace_cap_extra_radius = float(configFile.get("traceCapExtraRadius", 0.0))
            print(f"  processing {fname} ({layer_type}) — {len(coords)} pads — Z depth: {work_z:.2f}mm")
            print(f"  circular pads will use {pad_arc_code} full-circle arcs")
            print(f"  trace caps mode: {trace_cap_mode}")

            # deposit ink on each pad using raster fill based on aperture size
            for px, py, pad_size, pad_shape in coords:
                if pad_size <= nozzle_size:
                    g.rapid(z=safe_z)
                    g.rapid(point=(px, py))
                    g.rapid(z=work_z)
                    g.rapid(z=safe_z)
                else:
                    if pad_shape == 'C':
                        deposit_circular_pad_arcs(
                            g,
                            cx=px,
                            cy=py,
                            radius=pad_size / 2,
                            nozzle_size=nozzle_size,
                            work_z=work_z,
                            safe_z=safe_z,
                            clockwise=pad_arc_clockwise,
                        )
                    else:
                        lines = generate_pad_raster(px, py, pad_size, nozzle_size, pad_shape)
                        if lines:
                            g.rapid(z=safe_z)
                            g.rapid(point=(lines[0][0], lines[0][1]))
                            g.rapid(z=work_z)
                            for sx, sy, ex, ey in lines:
                                g.move(point=(sx, sy))
                                # g.move(point=(ex, ey))
                            g.rapid(z=safe_z)



            if traces:
                crossings = find_trace_intersections(traces)
                print(f"  processing {len(traces)} trace segments — {fill_passes} fill pass(es) — {len(crossings)} crossings")

                # layer 1: deposit all traces, minimize Z lifts between connected segments
                print(f"  depositing {len(traces)} trace segments")
                last_end = None
                for (x, y), (nx, ny), seg_width in traces:
                    seg_passes = calculate_fill_passes(seg_width, nozzle_size)
                    start_offsets = generate_fill_offsets(x, y, nx, ny, nozzle_size, seg_passes, trace_width=seg_width)
                    end_offsets   = list(reversed(generate_fill_offsets(nx, ny, x, y, nozzle_size, seg_passes, trace_width=seg_width)))
                    for k in range(seg_passes):
                        sx, sy = start_offsets[k]
                        ex, ey = end_offsets[k]
                        if last_end is None or math.dist(last_end, (sx, sy)) > 0.001:
                            g.rapid(z=safe_z)
                            g.rapid(point=(sx, sy))
                            g.rapid(z=work_z)
                        else:
                            g.move(point=(sx, sy))
                        g.move(point=(ex, ey))
                        last_end = (ex, ey)

                if trace_cap_mode != "none":
                    trace_caps = collect_trace_caps(traces, mode=trace_cap_mode, tolerance=trace_cap_tolerance)
                    print(f"  depositing {len(trace_caps)} semicircular trace cap(s) — mode: {trace_cap_mode}")
                    for cap_x, cap_y, interior_x, interior_y, cap_width in trace_caps:
                        deposit_trace_cap(
                            g,
                            cx=cap_x,
                            cy=cap_y,
                            interior_x=interior_x,
                            interior_y=interior_y,
                            trace_width=cap_width,
                            nozzle_size=nozzle_size,
                            work_z=work_z,
                            safe_z=safe_z,
                            clockwise=pad_arc_clockwise,
                            extra_radius=trace_cap_extra_radius,
                        )

                # layer 1 cure
                if layer_type == "copper":
                    print(f"  cure stage 1: dry 90C for 5min")
                    g.write(f"M190 S{int(active_head.get('cureDryTemp', 90))}")
                    g.sleep(cure_dry_seconds)
                    print(f"  cure stage 2: sinter 170C for 15min")
                    g.write(f"M190 S{int(active_head.get('cureTemp', 170))}")
                    g.sleep(cure_seconds)
                    g.write("M140 S0")
                    camera_sweep(g, safe_z, board_size_x, board_size_y, layer_index)

                if crossings:
                    insulator_head = get_head(configFile, "insulator")
                    ins_tool       = get_tool_number(insulator_head)
                    ins_size       = nozzle_size * 6
                    ins_work_z     = work_z + layer_height
                    ins_safe_z     = ins_work_z + 5
                    over_work_z    = work_z + layer_height + insulator_layer_height
                    over_safe_z    = over_work_z + 5
                    cap_work_z     = over_work_z + layer_height
                    cap_safe_z     = cap_work_z + 5
                    over_segs      = set()
                    for _, _, i, j in crossings:
                        over_segs.add(i)
                        over_segs.add(j)
                   
                    # layer 3: insulator over full length of both crossing traces
                    g.write(f"T{ins_tool}")
                    print(f"  depositing insulator over crossing trace segments")
                    for idx in over_segs:
                        (x, y), (nx, ny), _ = traces[idx]
                        g.rapid(z=ins_safe_z)
                        g.rapid(point=(x, y))
                        g.rapid(z=ins_work_z)
                        g.move(point=(nx, ny))
                        g.rapid(z=ins_safe_z)

                    # layer 3 cure
                    g.write(f"M190 S{int(insulator_head.get('cureTemp', 135))}")
                    g.sleep(insulator_head.get('cureSeconds', 600))
                    g.write("M140 S0")
                    camera_sweep(g, over_safe_z, board_size_x, board_size_y, layer_index)

                    # layer 5: conductive over-traces
                    g.write(f"T{tool_number}")
                    print(f"  redrawing over-traces at Z{over_work_z:.2f}")
                    for idx in over_segs:
                        (x, y), (nx, ny), seg_width = traces[idx]
                        seg_passes = calculate_fill_passes(seg_width, nozzle_size)
                        start_offsets = generate_fill_offsets(x, y, nx, ny, nozzle_size, seg_passes, trace_width=seg_width)
                        end_offsets   = list(reversed(generate_fill_offsets(nx, ny, x, y, nozzle_size, seg_passes, trace_width=seg_width)))
                        for k in range(seg_passes):
                            sx, sy = start_offsets[k]
                            ex, ey = end_offsets[k]
                            g.rapid(z=over_safe_z)
                            g.rapid(point=(sx, sy))
                            g.rapid(z=over_work_z)
                            g.move(point=(ex, ey))

                    if trace_cap_mode != "none" and over_segs:
                        over_trace_list = [traces[idx] for idx in over_segs]
                        over_trace_caps = collect_trace_caps(over_trace_list, mode=trace_cap_mode, tolerance=trace_cap_tolerance)
                        print(f"  depositing {len(over_trace_caps)} semicircular over-trace cap(s) — mode: {trace_cap_mode}")
                        for cap_x, cap_y, interior_x, interior_y, cap_width in over_trace_caps:
                            deposit_trace_cap(
                                g,
                                cx=cap_x,
                                cy=cap_y,
                                interior_x=interior_x,
                                interior_y=interior_y,
                                trace_width=cap_width,
                                nozzle_size=nozzle_size,
                                work_z=over_work_z,
                                safe_z=over_safe_z,
                                clockwise=pad_arc_clockwise,
                                extra_radius=trace_cap_extra_radius,
                            )

                    # layer 5 cure
                    if layer_type == "copper":
                        print(f"  cure over-traces stage 1: dry 90C for 5min")
                        g.write(f"M190 S{int(active_head.get('cureDryTemp', 90))}")
                        g.sleep(cure_dry_seconds)
                        print(f"  cure over-traces stage 2: sinter 170C for 15min")
                        g.write(f"M190 S{int(active_head.get('cureTemp', 170))}")
                        g.sleep(cure_seconds)
                        g.write("M140 S0")
                        camera_sweep(g, over_safe_z, board_size_x, board_size_y, layer_index)

                    # layer 7: insulator cap over full length of over-traces
                    g.write(f"T{ins_tool}")
                    print(f"  depositing insulator cap over over-trace segments")
                    for idx in over_segs:
                        (x, y), (nx, ny), _ = traces[idx]
                        g.rapid(z=cap_safe_z)
                        g.rapid(point=(x, y))
                        g.rapid(z=cap_work_z)
                        g.move(point=(nx, ny))
                        g.rapid(z=cap_safe_z)

                    # layer 7 cure
                    g.write(f"M190 S{int(insulator_head.get('cureTemp', 135))}")
                    g.sleep(insulator_head.get('cureSeconds', 600))
                    g.write("M140 S0")
                    camera_sweep(g, over_safe_z, board_size_x, board_size_y, layer_index)

            else:
                print(f"  no traces found in {fname}")


            # deposit insulator between copper layers in multi mode
            if layer_mode == "multi" and layer_index < board_layers - 1:
                print(f"  depositing insulator between layers")
                deposit_insulator(g, coords, work_z, safe_z, nozzle_size, configFile)

            layer_index += 1

        # end program
        g.tool_off()
        g.rapid(x=0, y=0)
        g.stop()

    print(f"G-code written to {output_file}")


if __name__ == "__main__":
    run()