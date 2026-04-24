# CIP Slicer — Developer Notes

## What This Does
Takes a Gerber PCB file (.zip or .gbr) and converts it to G-code 
for the conductive ink printer. Reads pad coordinates layer by layer 
and outputs machine movement commands.

## How to Run
```bash
cd src
python3 slicerSoftware.py
```
Output is written to `TestFiles/test-gbr.gcode` (path driven by `config.json`)

## Config (src/config.json)
| Key | Description | Status |
|-----|-------------|--------|
| `units` | mm or inches | ✓ |
| `maxBedSize` | [x, y, z] machine limits in mm | ✓ |
| `printSpeed` | speed in mm/s | ✓ |
| `gerberFile` | path to input .zip | ✓ |
| `steps_per_mm_x/y` | motor steps per mm | ⚠ PLACEHOLDER - confirm with hardware team |
| `cure_time_seconds` | dwell time after each ink layer | ⚠ PLACEHOLDER - confirm with hardware team |

## Layer Order (future implementation)
- Layer 1 — Conductive ink
- Layer 1.5 — Cure
- Layer 2 — Camera sweep
- *(single layer boards stop here)*
- Layer 3 — Insulator
- Layer 3.5 — Cure
- Layer 4 — Camera sweep (check for shorts)
- *(single side boards stop here)*
- Layer 5 — Conductive ink
- Layer 5.5 — Cure
- Layer 6 — Camera sweep
- Repeat for n layers

> Note: insulator cover type determines if stop is at layer 3.5 or 4

## How Parsing Works
1. Load `.gbrjob` file using pygerber — this gives us all layer filenames + types
2. For each copper layer, extract X/Y pad coordinates using regex on raw Gerber source
3. Normalize coordinates so bottom-left pad starts at (0, 0)
4. Convert coordinates to G-code moves using gscrib

## Coordinate System
- Gerber format 4.6: raw values are in 1/1,000,000 mm — divide by 1,000,000 to get mm
- `coord_to_steps(x, y)` converts mm → machine steps using steps_per_mm from config
- All moves use absolute positioning (G90)

## What's Done
- [x] pygerber installed and parsing Gerber files
- [x] Coordinate extraction and normalization
- [x] Layer type detection via `file_function` from job file
- [x] Base G-code generation (copper layers only)
- [x] Cure dwell after each copper layer
- [x] Output path driven by config.json

## What's TODO
- [ ] Unzip input from config path automatically
- [ ] Single vs multi layer mode flag in config
- [ ] Camera sweep G-code pattern (need from camera team)
- [ ] Real steps/mm values (need from hardware team)
- [ ] Real cure time (need from hardware team)
- [ ] Edge case handling (empty layers, out of bounds)
- [ ] Config validation before running

## Parsing Notes (from original planning)
- Use pygerber to parse layer by layer
- Convert each parsed section to a G-code step as we go
- Need max X/Y of printer (in config as `maxBedSize`)
- Need nozzle thickness for fill calculations

### Fill Logic (TODO)
For lines:
- Divide line width by nozzle thickness to get number of passes needed
- Loop: `for pass in range(width / nozzle_size)` to fill in

For curves:
- Still researching curve reading in pygerber and generation in gscrib
- Possible approach: split curves into line segments (integration approximation)
- Need to look into pygerber AST nodes for arc/curve primitives

## Dependencies
- `pygerber==3.0.0a4` — Gerber file parsing
- `gscrib==1.2.0` — G-code generation
- `pillow` — image rendering (for visual verification)

## Important G-code Commands Reference

### Motion Commands
| Command | Description | Example |
|---------|-------------|---------|
| `G0` | Rapid move — fast positioning, no cutting | `G0 X10 Y5` |
| `G1` | Linear move — controlled speed, used for ink deposition | `G1 Z-2 F600` |
| `G2` | Circular move clockwise | `G2 X10 Y0 I5 J0` |
| `G3` | Circular move counterclockwise | `G3 X10 Y0 I5 J0` |

### Setup Commands
| Command | Description | Example |
|---------|-------------|---------|
| `G21` | Set units to millimeters | `G21` |
| `G20` | Set units to inches | `G20` |
| `G90` | Absolute positioning — all moves from origin | `G90` |
| `G91` | Relative positioning — all moves from current position | `G91` |
| `G92` | Set current position as origin | `G92 X0 Y0 Z0` |
| `G17` | Work in XY plane (default) | `G17` |
| `G28` | Return to home position | `G28 X0 Y0` |

### Timing & Tool Commands
| Command | Description | Example |
|---------|-------------|---------|
| `G4` | Dwell — pause for a set time | `G04 P30` (30 seconds) |
| `M3` | Spindle/tool on clockwise | `M03 S1000` (1000 RPM) |
| `M5` | Spindle/tool off | `M05` |
| `M8` | Flood coolant on | `M08` |
| `M9` | Flood coolant off | `M09` |

### Program Commands
| Command | Description | Example |
|---------|-------------|---------|
| `M0` | Program stop (pause) | `M00` |
| `M2` | End of program, no reset | `M02` |
| `M30` | End of program, reset to start | `M30` |

### Parameters
| Parameter | Description | Example |
|-----------|-------------|---------|
| `X` `Y` `Z` | Axis coordinates in mm | `G0 X10 Y5 Z0` |
| `F` | Feed rate in mm/min | `G1 Z-2 F600` |
| `S` | Spindle speed in RPM | `M03 S1000` |
| `P` | Dwell time in seconds | `G04 P30` |
| `I` `J` | Arc center offset from current point (X and Y) | `G2 X10 Y0 I5 J0` |

### What Our Slicer Currently Uses
| Command | Where |
|---------|-------|
| `G21` | Set mm at start |
| `G90` | Absolute mode at start |
| `G92 X0 Y0 Z0` | Set origin at start |
| `G0` | Rapid move to pad position |
| `G1 Z-2` | Press down to deposit ink |
| `G0 Z5` | Lift to safe height |
| `G04 P30` | Cure dwell after each copper layer |
| `M03 S1000` | Tool on at start |
| `M05` | Tool off at end |
| `M02` | End of program |
