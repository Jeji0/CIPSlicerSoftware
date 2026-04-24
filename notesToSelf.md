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
