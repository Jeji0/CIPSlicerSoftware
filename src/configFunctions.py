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
    
    "maxBedSize": [
        220.0,
        220.0,
        250.0
    ],
    "copperWorkZ": 0.2,
    "insulatorWorkZ": 0.4,
    "crossoverWorkZ": 0.6,
    "pasteWorkZ": 28.9,
    "cameraWorkZ": 39,
    "substrateHeight": 0.9,
    "hopClearance": 5,
    "layerMode": "single",
    "printSpeed": 60.0,
    "printFeedRate": 3600.0,
    "gerberFile": "TestFiles/NE555Circuit5.zip",
    "gerberJobFile": "",
    "steps_per_mm_x": 80,
    "steps_per_mm_y": 80,
    "steps_per_mm_z": 400,
    "pullpush": 5.0,
    "pullpush_speed": 200,
    "paste_pullpush": 60.0,
    "paste_pullpush_speed": 200,
    "pasteDotE": 40,
    "retractionDistance": 0.5,
    "traceEdgeInset": 0.05,
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
            "nozzleSize": 0.2,
            "traceWidth": 0.2,
            "cureDryTemp": 90.0,
            "cureDrySeconds": 300.0,
            "cureTemp": 180.0,
            "cureSeconds": 900.0,
            "flowRate": 0.05,
            "layerHeight": 0.2,
            "flowScaleByWidth": True
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
            "flowScaleByWidth": True
        },
        {
            "id": "paste",
            "name": "Solder Paste",
            "type": "paste",
            "toolNumber": 1,
            "nozzleSize": 0.3,
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
    "secondInkLayer": False,
    "secondInkLayerZOffset": 0.1,
    "toggles": {
        "toggle_tool_change": True,
        "toggle_heating": False,
        "toggle_camera_sweep": False,
        "toggle_crossover": False,
        "toggle_arc_moves": True,
        "toggle_extrusion": False,
        "toggle_purge": False,
        "toggle_conductive": True,
        "toggle_paste": True,
        "toggle_ink_traces_only": False,
        "toggle_gerber_transfer": False,
        "toggle_second_ink": True
    },
    "pasteRetractE": 0
}

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