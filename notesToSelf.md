# CIP Slicer Software

The slicer is designed to parse Gerber PCB files into G-code for the conductive ink printer. It takes in Gerber files alongside parameters such as printer information, ink properties, and layer attributes to determine how many layers need to be printed, when a cure cycle is needed, and when an insulator layer needs to be added to prevent shorts and unintended connections.

## How to Run
```bash
cd src
python3 slicerSoftware.py
```
Output is written to `TestFiles/test-gbr.gcode` (path driven by `config.json`)

## How It Works
1. Loads and validates `config.json`
2. Reads board info from the `.gbrjob` file (board size, layer count, copper thickness)
3. Auto-unzips the Gerber file package if needed
4. Loops through each copper layer and extracts pad positions (D03) and trace points (D01)
5. Normalizes coordinates to fit within the board bounds
6. Generates G-code using gscrib — moves, ink deposition, cure dwells, camera sweep triggers
7. Writes output `.gcode` file derived from the input zip name in config

## Gerber File Information
The slicer reads a series of Gerber files and decodes pad positions and trace paths from each copper layer. It uses the `.gbrjob` job file to identify layer types (copper, mask, paste, silkscreen, edge cuts) and only processes copper layers for ink deposition. Coordinates are extracted in Gerber format 4.6 (1/1,000,000 mm units) and normalized so the bottom-left pad starts at (0, 0).

## Printer Information
Printer parameters are loaded from `config.json` and include the build area, steps per mm for each axis (Ender 3 defaults: X/Y = 80, Z = 400), print speed, and layer height. The slicer uses these to validate that all coordinates fit within the machine's physical limits before generating any G-code.

## Ink Properties — Conductor 3
The slicer is configured for Voltera Conductor 3 silver-based conductive ink. Key properties:
- Approximately 30% as conductive as bulk copper
- No burnishing needed
- No board flipping needed — cures face up
- Two-stage cure process:
  - Stage 1: dry at 90°C for 5 minutes
  - Stage 2: sinter at 170°C for 15 minutes
- Cure times are stored in `config.json` as `cure_dry_seconds` and `cure_seconds`

## Layer Order
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

Note: insulator cover type determines if stop is at layer 3.5 or 4

## Layer Count
The number of layers is determined by the `layerMode` setting in `config.json`:
- `"single"` — top copper layer only (F_Cu)
- `"multi"` — all copper layers (F_Cu + B_Cu + inner layers)

If `layerMode` is not set in config, it is auto-detected from the `.gbrjob` file — boards with more than 1 layer default to multi mode.

## Camera + Computer Vision
After each ink + cure sequence, the slicer triggers a camera sweep. The camera system uses computer vision to check for shorts and verify ink coverage before proceeding to the next layer. The slicer passes the board dimensions and current layer index to the CV system and expects a pass/fail response. If the CV system returns a fail, the print stops.

The sweep pattern and CV communication protocol are pending confirmation from the camera team.

## Config Reference (src/config.json)
| Key | Description | Value |
|-----|-------------|-------|
| `units` | Unit system | `"mm"` |
| `maxBedSize` | Machine build volume [x, y, z] in mm | `[220, 220, 250]` |
| `layerHeight` | Height per ink layer in mm | `0.2` |
| `layerMode` | Single or multi layer mode | `"single"` or `"multi"` |
| `printSpeed` | Print speed in mm/s | `60` |
| `gerberFile` | Path to input Gerber zip | `"TestFiles/test-gbr.zip"` |
| `gerberJobFile` | Path to .gbrjob file | `"TestFiles/test-job.gbrjob"` |
| `steps_per_mm_x` | X axis steps per mm (Ender 3 default) | `80` |
| `steps_per_mm_y` | Y axis steps per mm (Ender 3 default) | `80` |
| `steps_per_mm_z` | Z axis steps per mm (Ender 3 default) | `400` |
| `cure_dry_temp` | Cure stage 1 temperature in °C | `90` |
| `cure_dry_seconds` | Cure stage 1 duration in seconds | `300` |
| `cure_temp` | Cure stage 2 temperature in °C | `170` |
| `cure_seconds` | Cure stage 2 duration in seconds | `900` |

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

### Timing and Tool Commands
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
| `I` `J` | Arc center offset from current point | `G2 X10 Y0 I5 J0` |

### What Our Slicer Currently Outputs
| Command | Where |
|---------|-------|
| `G21` | Set mm at start |
| `G90` | Absolute mode at start |
| `G92 X0 Y0 Z0` | Set origin at start |
| `G0` | Rapid move to pad/trace position |
| `G1 Z` | Press down to deposit ink |
| `G0 Z` | Lift to safe height |
| `G04 P300` | Cure stage 1 dwell |
| `G04 P900` | Cure stage 2 dwell |
| `M03 S1000` | Tool on at start |
| `M05` | Tool off at end |
| `M02` | End of program |

## What is Done
- [x] pygerber installed and parsing Gerber files
- [x] Board info extracted from job file (size, layers, copper thickness)
- [x] Coordinate extraction and normalization (pads + traces)
- [x] Layer type detection via file_function from job file
- [x] Base G-code generation (copper layers only)
- [x] Conductor 3 two-stage cure sequence
- [x] Single vs multi layer mode
- [x] Auto-unzip from config path
- [x] Z height per layer from config
- [x] Config validation
- [x] Out of bounds detection using real board size
- [x] Camera sweep stub with CV pass/fail interface

## What is TODO
- [ ] Real camera sweep pattern (pending camera team)
- [ ] CV system communication implementation (pending camera team)
- [ ] Hardware testing on real Ender 3 setup
- [ ] Confirm steps/mm if machine has been recalibrated
- [ ] Insulator layer G-code
- [ ] Nozzle width fill logic for wide traces

## Parsing Notes
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
- `pillow` — image rendering for visual verification
