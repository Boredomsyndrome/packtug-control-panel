# Vision calibration assets

`charuco_5x7_30mm_a4_300dpi.png` is the wrist-camera hand-eye calibration board.
Print it on A4 at **100% / actual size**. The black-and-white board itself must
measure **150 x 210 mm** (5 x 7 squares, 30 mm per square); do not use “fit to
page”. Mount it flat and keep it fixed while capturing 10–15 varied, stationary
arm poses in the Arm page.

The runtime carton marker is the **35 x 35 mm QR code**, excluding its quiet
zone. Its known size and the D455 RGB intrinsics are used for PnP after the
camera-to-tool transform has been solved.

Runtime target coordinates apply the QR-local offset configured in the Arm
page. QR-local X points right across the printed code, Y points toward its top,
and Z points out of the printed face. The default Y offset of 52.5 mm is only
for the current upright paper block with its QR centre at 35 mm and gripper web
at 87.5 mm above the box floor.

Regenerate the board with:

```bash
python docs/calibration/generate_charuco_board.py
```

The generator needs OpenCV with the ArUco module and Pillow. Do not resize the
generated PNG after printing a physical board from it.
