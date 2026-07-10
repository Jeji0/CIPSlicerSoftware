import json
import os

# absolute path to config.json — always in the same folder as this file
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def unitConv(value, unit):
    if unit == "mm":
        return value
    elif unit == "in":
        return value * 25.4
    elif unit == "mil":
        return value * 0.0254
    else:
        raise ValueError("Invalid unit. Please use 'mm', 'in', or 'mil'.")

defUnits = "mm"

defMaxBed = [unitConv(220, defUnits),
             unitConv(220, defUnits),
             unitConv(250, defUnits)]
defLayHei = unitConv(0.2, defUnits)
defPriSpe = unitConv(60, defUnits)

printProp = {
    {
    "units": "mm",
    "maxBedSize": [
        220.0,
        220.0,
        250.0
    ],
    "layerHeight": 0.2,
    "conductiveLayerHeight": 0.3,
    "insulatorLayerHeight": 0.2,
    "copperWorkZ": 14.8,
    "insulatorWorkZ": 0.4,
    "crossoverWorkZ": 0.6,
    "pasteWorkZ": 46.5,
    "cameraWorkZ": 54,
    "boardThickness": 1,
    "printHeightOffset": 0.5,
    "layerMode": "single",
    "printSpeed": 60.0,
    "printFeedRate": 3600.0,
    "gerberFile": "TestFiles/NE555Circuit5.zip",
    "gerberJobFile": "",
    "steps_per_mm_x": 80,
    "steps_per_mm_y": 80,
    "steps_per_mm_z": 400,
    "pullpush": 2.0,
    "pullpush_speed": 500,
    "paste_pullpush": 1.0,
    "paste_pullpush_speed": 300,
    "activeHeads": [
        "conductor3",
        "si3104",
        "paste",
        "camera"
    ],
    "heads": [
        {
            "id": "conductor3",
            "name": "Voltera Conductor 3",
            "type": "conductive",
            "toolNumber": 0,
            "nozzleSize": 0.2032,
            "traceWidth": 0.2032,
            "cureDryTemp": 90,
            "cureDrySeconds": 300,
            "cureTemp": 170,
            "cureSeconds": 900,
            "flowRate": 0.05,
            "layerHeight": 0.2,
            "flowScaleByWidth": true
        },
        {
            "id": "si3104",
            "name": "ACI SI3104 Insulator",
            "type": "insulator",
            "toolNumber": 4,
            "nozzleSize": 0.225,
            "traceWidth": 0.225,
            "cureTemp": 135,
            "cureSeconds": 600,
            "offsetX": 0,
            "offsetY": 0,
            "flowRate": 0.04,
            "layerHeight": 0.2,
            "flowScaleByWidth": true
        },
        {
            "id": "paste",
            "name": "Solder Paste",
            "type": "paste",
            "toolNumber": 1,
            "nozzleSize": 0.3,
            "dwellFactor": 0.5,
            "cureTemp": 0,
            "cureSeconds": 0
        },
        {
            "id": "camera",
            "name": "Camera Head",
            "type": "camera",
            "toolNumber": 2
        },
        {
            "id": "nc/pen",
            "name": "NC/Pen Head",
            "type": "nc/pen",
            "toolNumber": 3
        }
    ],
    "nozzleSize": 0.2,
    "traceWidth": 0.25,
    "cure_dry_temp": 90,
    "cure_dry_seconds": 300,
    "cure_temp": 170,
    "cure_seconds": 900,
    "insulator_cure_temp": 135,
    "insulator_cure_seconds": 600,
    "insulator_head_offset_x": 0,
    "insulator_head_offset_y": 0,
    "copperWorkZ": 0.4,
    "pasteWorkZ": 0.6,
    "insulatorWorkZ": 0.4,
    "crossoverWorkZ": 0.6,
    "traceEdgeInset": 0.05
}}

filePath = {
    "gerberFile": "TestFiles/test-gbr.zip"
}

def updConf(input):
    with open(CONFIG_PATH, "r") as configFile:
        data = json.load(configFile)
        data.update(input)
    with open(CONFIG_PATH, "w") as configFile:
        json.dump(data, configFile, indent=4)

def defConfig():
    with open(CONFIG_PATH, "w") as configFile:
        json.dump({}, configFile, indent=4)
    updConf(printProp)
    updConf(filePath)

#defConfig()
