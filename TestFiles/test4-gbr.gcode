; TOTAL_LINES:11
; --- BEGIN PRINT ---
G21 ; set units to millimeters
G90 ; absolute coordinates
M82 ; absolute extrusion
G28 ; home all axes
G92 E0 ; reset E axis
F3600.0 ; set print feed rate

; --- END PRINT ---
G28 X0 Y0 ; home X and Y
M84 ; disable motors
