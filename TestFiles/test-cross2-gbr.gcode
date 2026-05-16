G92 X0 Y0 Z0 ; Set axis position
G21 ; Set length units, millimeters
G90 ; Set distance mode, absolute
F600
G28  ; Auto-home axes
G0 Z5
S1000 M03 ; Start tool, clockwise
G04 P1 ; Sleep for a while, seconds
M05 ; Stop tool
G0 X0 Y0
M02 ; End of program, no reset
