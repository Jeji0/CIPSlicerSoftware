; TOTAL_LINES:12
; --- BEGIN PRINT ---
G21 ; set units to millimeters
G90 ; absolute coordinates
M82 ; absolute extrusion
G28 ; home all axes
G92 E0 ; reset E axis
F3600.0 ; set print feed rate
T0 ; conductive head

; --- END PRINT ---
G28 X0 Y0 ; home X and Y
M84 ; disable motors
