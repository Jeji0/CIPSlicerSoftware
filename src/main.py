import configFunctions as cF
from GUI import GUI
import os

def main():
    if not os.path.exists("config.json"):
        cF.defConfig()
        print("Default configuration created.")
    else:
        print("Existing configuration loaded.")
    GUI()

if __name__ == "__main__":
    main()
