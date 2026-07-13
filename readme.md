# CIP Slicer Software

Slicer for the **Conductive Ink Printer (C.I.P.)** — a modified Creality Ender 3 that
fabricates PCBs by dispensing conductive ink and solder paste. Takes KiCad Gerber
exports (zipped) and generates G-code for the quick-swappable heads: conductive ink,
solder paste, insulator and camera inspection head.

## Requirements

- Python **3.12** (tested on macOS)
- Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

From the `src/` folder:

```bash
python3 GUI.py        # graphical interface
python3 main.py       # command-line entry
```

Typical workflow: **Browse** to a Gerber zip → set substrate height / nozzle →
choose Debug/Testing toggles → **Preview PCB** to sanity-check → **Generate G-code**.
Output is written next to the zip as `<name>.gcode`.

Settings persist to `config.json` via **Save settings** — including the
Debug/Testing toggle states. Ink settings (nozzle size, cure temps/times) are
saved into the currently selected **Active head**'s entry.

## Layer order and Z scheme

```
1. Conductive ink (traces + pads)        copperWorkZ
   1.5 cure: dry 90°C 5min → sinter 170°C 15min
2. Camera sweep (vision inspection)       cameraWorkZ
3. Solder paste (dot per pad)             pasteWorkZ
4. Insulator                              insulatorWorkZ
5. Crossover ink layer                    crossoverWorkZ
```

All work heights are **absolute carriage positions including head length**, plus
`substrateHeight`. Travel between features happens at `workZ + hopClearance`.

Tool numbers: ink = T0, paste = T1, camera = T2, NC/pen = T3, insulator = T4.
`validate_config` rejects duplicate tool numbers among active heads.

## config.json reference

| Key | Meaning |
|---|---|
| `copperWorkZ`, `pasteWorkZ`, `insulatorWorkZ`, `crossoverWorkZ` | dispense height per layer (mm, absolute) |
| `cameraWorkZ` | camera standoff height |
| `substrateHeight` | added to every work height; set per substrate stack |
| `hopClearance` | travel lift above work height between features |
| `traceEdgeInset` | narrows ink traces by this much per side (0 = full width) |
| `pasteDotE` | E amount pushed per solder-paste dot |
| `pullpush` / `pullpush_speed` | ink prime/retract amount and feed |
| `paste_pullpush` / `paste_pullpush_speed` | same for paste travel moves |
| `retractionDistance` | E retract after each path (extrusion mode) |
| `heads` / `activeHeads` | per-head nozzle, tool number, cure profile |
| `gerberFile` / `gerberJobFile` | input paths (job file optional, for Altium) |

Machine-behavior constants not meant to change per print (prime cycles, park
position, cool-down time, camera grid spacing, arc-fit tolerances) live in the
**TUNABLE CONSTANTS** block at the top of `slicerSoftware.py`.

## Process tuning cheat sheet

- **Traces too wide** → raise `traceEdgeInset` (narrows each side). Careful:
  traces narrower than `nozzle + 2×inset` stop generating fill.
- **Paste dots too big/small** → adjust `pasteDotE`.
- **New substrate** → change `substrateHeight` only; work heights stay put.
- **Nozzle change** → set it in the GUI (writes to the head entry); trace width
  tracks nozzle size automatically.

## Files

| File | Purpose |
|---|---|
| `slicerSoftware.py` | Gerber parsing, toolpath generation, G-code output |
| `GUI.py` | tkinter interface, preview, settings persistence |
| `configFunctions.py` | config read/write helpers + factory defaults (`defConfig`) |
| `config.json` | all per-print settings (source of truth) |

## Known limitations

- Paste is dispensed only for apertures present in the paste Gerber — copper
  drawn as graphic lines (no pad object in the footprint) gets no paste.
  Fix in KiCad by using real pads or adding F.Paste graphics.
- G36/G37 filled regions are approximated by their bounding rectangle.
- Traces narrower than the fill threshold are skipped silently — check the
  preview against the Gerber for thin-trace boards.
