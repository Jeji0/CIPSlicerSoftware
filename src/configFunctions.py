import json

def unitConv(value, unit):  #cosidered mm by default
    if unit == "mm":
        return value
    elif unit == "in": 
        return value * 25.4 #in -> mm
    if unit == "mil":
        return value * 0.0254 #mil -> mm
    else:
        raise ValueError("Invalid unit. Please use 'mm', 'in', or 'mil'.")

defUnits = "mm" #default units

#defaultConfigValues.py
    #Head Properties
        #Conductive Ink
defCurTemCon = 170 #Celcius
defCurTimCon = 15*60 #seconds
condInkHeadNozDiam = unitConv(0.4, defUnits) 

    #Printer Properties
defMaxBed = [unitConv(220, defUnits), #X in mm
             unitConv(220, defUnits), #Y in mm
             unitConv(250, defUnits)] #Z in mm
defLayHei = unitConv(0.2, defUnits) #mm
defPriSpe = unitConv(60, defUnits) #mm/s
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

    #Nozzle Properties


    #Default File Path
defFilePath = "TestFiles\test-gbr.zip"
filePath = {
    "gerberFile": defFilePath
}






def updConf(input):
    with open("config.json", "r") as configFile:
        data = json.load(configFile)
        data.update(input)
    with open("config.json", "w") as configFile:
        json.dump(data, configFile, indent=4)
    

def defConfig():
    with open("config.json", "w") as configFile:
        json.dump({}, configFile, indent=4)
    updConf(printProp)
    updConf(filePath)

#defConfig()
