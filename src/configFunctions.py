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
    "units": defUnits,
    "maxBedSize": defMaxBed,
    "layerHeight": defLayHei,
    "printSpeed": defPriSpe,
    "layerMode": "single",
    "steps_per_mm_x": 80,
    "steps_per_mm_y": 80,
    "steps_per_mm_z": 400,
    "nozzleSize": 0.225,
    "traceWidth": 0.25,
    "cure_dry_temp": 90,
    "cure_dry_seconds": 300,
    "cure_temp": 170,
    "cure_seconds": 900,
    "insulator_cure_temp": 135,
    "insulator_cure_seconds": 600,
    "insulator_head_offset_x": 0,
    "insulator_head_offset_y": 0
}

filePath = {
    "gerberFile": "TestFiles/test-gbr.zip",
    "gerberJobFile": "TestFiles/test-job.gbrjob"
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
