# Packing targets and level placement

The recorded hardware **internal** cage dimensions are **550 W × 250 D ×
400 H mm**, defined by `CAGE_BIN` in `src/data/mock.ts`. Packing, the 3D view
fallback and the Arm page defaults share these frontend dimensions. The Python
`CageIn` defaults have the same size in metres. Taught corners override the
backend defaults; the saved teaching must be fitted before sending a packing
target. No saved calibration is overwritten by these defaults.

Packing uses the front-left floor corner, X across the width, Y into the cage,
and Z upwards. The arm cage frame uses the **opening floor centre**. For a
packing-local target `(u, v, w)` in millimetres and taught width `W` in metres:

```
local_x = u / 1000 - W / 2
local_y = v / 1000
local_z = w / 1000
arm_xyz = taught_origin + Rz(taught_yaw) * local_xyz
```

There is no scaling of boxes to fit different taught dimensions. The Packing
page displays both sets of dimensions and reports differences greater than
1 mm. This display threshold is not a calibration acceptance tolerance.
Boxes outside the taught volume and targets without the required wall
clearance cannot be sent. Pending or inconsistent corner fits are blocked.

The upright `DEMO-BOX` is 152 × 85 × 110 mm. Its gripper web target is
`(x + 76, y + 20, z + 87.5)` mm. These offsets are not applied to other SKUs
or rotated demo boxes. The five targets in the opening-centred cage frame are:

| Box | X (mm) | Y (mm) | Z (mm) |
| --- | ---: | ---: | ---: |
| Back left | -80 | 180 | 87.5 |
| Back right | 81 | 180 | 87.5 |
| Top centre | 0 | 180 | 197.5 |
| Front left | -80 | 85 | 87.5 |
| Front right | 81 | 85 | 87.5 |

The complete demo occupies X=119..432, Y=65..245 and Z=0..220 mm in packing
coordinates. It fits the recorded internal dimensions. Its boxes have only
5 mm clearance from the back wall; this is distinct from the 70 mm clearance
of the web target. The existing collision checks model a tool point, not the
swept volume of a carried box, gripper and robot links. Ordinary bin packing
also permits box rotations and does not establish a calibrated physical grasp;
box-centre targets are geometric references only.

Placement uses the panel's Ned side-entry convention: **roll 180°, pitch 0°,
yaw = cage yaw + 90°** (wrapped to ±180°). The executor levels at safe home,
then uses that attitude through entry, approach and release. It checks reported
attitude within 2° after leveling and each entry move and before release. An
unreachable linear move stops; entry/leveling cannot fall back to a joint-space
route or the previous attitude. Optional return follows the checked route in
reverse with the same attitude. Legacy `preserve_orientation` cannot override
this constraint; J2 folding during this sequence is rejected before motion.

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
