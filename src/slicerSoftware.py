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

# steps per mm tells the machine how many motor steps equal 1mm of movement
steps_per_mm_x = configFile.get("steps_per_mm_x", 80)
steps_per_mm_y = configFile.get("steps_per_mm_y", 80)

def coord_to_steps(x_mm: float, y_mm: float) -> tuple[int, int]:
    """Convert Gerber mm coordinates to machine step counts."""
    return round(x_mm * steps_per_mm_x), round(y_mm * steps_per_mm_y)

# load the full Gerber project from the job file (contains all layers)
gerber_job = GerberJobFile.from_file(os.path.join(BASE_DIR, "../TestFiles/test-job.gbrjob"))
project = gerber_job.to_project()

# derive the output .gcode filename from the zip path in config
# e.g. TestFiles/test-gbr.zip -> TestFiles/test-gbr.gcode
gerber_zip_path = configFile.get("gerberFile", "TestFiles/test-gbr.zip")
gerber_dir      = os.path.join(BASE_DIR, "..", os.path.dirname(gerber_zip_path))
gerber_name     = os.path.splitext(os.path.basename(gerber_zip_path))[0]
output_file     = os.path.join(gerber_dir, gerber_name + ".gcode")

# load just the front copper layer to extract pad positions
gerber_file = GerberFile.from_file(os.path.join(BASE_DIR, "../TestFiles/test-F_Cu.gbr"))

# extract X/Y coordinates from the raw Gerber source using regex
# Gerber format 4.6 means values are in units of 1/1,000,000 mm so we divide to get mm
coords = []
for match in re.finditer(r'X(-?\d+)Y(-?\d+)D03', gerber_file.source_code):
    x = int(match.group(1)) / 1_000_000
    y = int(match.group(2)) / 1_000_000
    coords.append((x, y))

# normalize so the bottom-left pad is at (0, 0) — keeps moves within bed bounds
min_x = min(c[0] for c in coords)
min_y = min(c[1] for c in coords)
coords = [(x - min_x, y - min_y) for x, y in coords]

# write G-code using gscrib
with GCodeBuilder(output=output_file) as g:
    # define the machine's physical movement limits
    g.set_bounds("axes", min=(0, 0, -50), max=(configFile["maxBedSize"][0], configFile["maxBedSize"][1], 50))
    g.set_axis(point=(0, 0, 0))          # set current position as origin
    g.set_length_units("millimeters")
    g.set_time_units("seconds")
    g.set_distance_mode("absolute")      # all moves are absolute, not relative
    g.set_feed_rate(configFile.get("printSpeed", 60) * 10)  # convert mm/s to mm/min

    g.rapid(z=5)                         # lift to safe height before moving
    g.tool_on("clockwise", 1000)         # start the tool at 1000 rpm
    g.sleep(1)                           # wait 1 second for tool to spin up

    for x, y in coords:
        g.rapid(point=(x, y))            # move to pad position at full speed
        g.move(z=-2)                     # press down to deposit ink
        g.rapid(z=5)                     # lift back to safe height

    g.tool_off()                         # stop the tool
    g.rapid(x=0, y=0)                    # return to origin
    g.stop()                             # end the program

print(f"G-code written to {output_file}")
