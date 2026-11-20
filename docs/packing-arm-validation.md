# Packing targets and level placement

The recorded hardware **internal** cage dimensions are **550 W × 250 D ×
400 H mm**, defined by `CAGE_BIN` in `src/data/mock.ts`. Packing, the 3D view
fallback and the Arm page defaults share these frontend dimensions. The Python
`CageIn` defaults have the same size in metres. Taught corners override the
backend defaults; the saved teaching must be fitted before sending a packing
target. No saved calibration is overwritten by these defaults.

Packing uses the front-left floor corner, X across the width, Y into the cage,
and Z upwards. The arm cage frame uses the **opening floor centre**. Placement
coordinates are expressed in the packing plan, so centre them with the packing
bin width `W_pack`; the taught cage supplies the world origin and yaw:

```
local_x = u / 1000 - W_pack / 2
local_y = v / 1000
local_z = w / 1000
arm_xyz = taught_origin + Rz(taught_yaw) * local_xyz
```

There is no scaling of boxes to fit different taught dimensions. The Packing
page displays both sets of dimensions and reports differences greater than
1 mm. This display threshold is not a calibration acceptance tolerance.
Boxes outside the taught volume and targets without the required wall
clearance cannot be sent. Pending or inconsistent corner fits are blocked.
A front face exactly on the opening plane (`Y = 0`) is allowed; negative Y is
outside the cage.

Each selected placement is converted independently; there is no fixed taught
final pose. For the upright `DEMO-BOX` (152 × 85 × 110 mm), the software
placement target assumes the gripper end is at the **centre of the box front
face**:

```
packing target = (x + w/2, y, z + h/2)   # front face, mid-height
```

The five targets in the opening-centred cage frame are:

| Box | X (mm) | Y (mm) | Z (mm) |
| --- | ---: | ---: | ---: |
| Back left | -80 | 160 | 55 |
| Back right | 81 | 160 | 55 |
| Top centre | 0 | 160 | 165 |
| Front left | -80 | 65 | 55 |
| Front right | 81 | 65 | 55 |

The complete demo occupies X=119..432, Y=65..245 and Z=0..220 mm in packing
coordinates. It fits the recorded internal dimensions. The front-face target
is 90 mm from the back wall for the back row and 185 mm for the front row.
The existing collision checks model a tool point, not the swept volume of a
carried box, gripper and robot links. Ordinary bin packing also permits box
rotations and does not establish a calibrated physical grasp; box-centre
targets are geometric references only.

Placement uses the table-level side-entry convention: **roll 0°, pitch 0°,
yaw = cage yaw + 90°** (wrapped to ±180°). The executor levels roll/pitch at
the grasp pose before closing, then keeps that horizontal attitude while
retracting, lowering and moving to safe home; yaw remains free until final cage
alignment. It uses the target attitude through entry, approach and release,
and checks reported attitude within 2° after each leveling/entry move and
before release. An unreachable linear move stops; entry/leveling cannot fall
back to a joint-space route or the previous attitude. Optional return follows
the checked route in reverse with the same attitude. The placement attitude is
mandatory; J2 folding during this sequence is rejected before motion.

Offline checks:

```
node --test tests/digitalTwin.test.mjs
python -m unittest discover -s tests -p 'test_*.py'
npm run typecheck
npm run build
python -m py_compile server/arm_server.py server/camera_stream.py
```

These verify software conventions and recorded sizes, not physical measurements
or tool mounting. Backend changes require a service restart; old transferred
targets must be regenerated. No hardware movement is part of these checks.
