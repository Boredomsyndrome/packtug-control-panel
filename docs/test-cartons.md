# Physical test carton layout

One manual layout (`ORD-TEST-MANUAL`) uses the six physical cartons in one layer,
without stacking:

- Left rear: two `TEST-BOX-B` in a depth row.
- Left front: two `TEST-BOX-A` in front of the B row.
- Right rear: one `TEST-BOX-C`, against the inside of the cage.
- Right front: one `DEMO-BOX`.

All cartons keep the 80 x 40 mm grip opening on the front face. The carton
thickness points into the cage: 55 mm for A/B/C and 85 mm for `DEMO-BOX`.

## Carton set

| SKU | Outer W x D x H | Front face | Grip opening | Qty |
|---|---|---:|---:|---:|
| `TEST-BOX-A` | 200 x 55 x 120 mm | 200 x 120 mm | 80 x 40 mm | 2 |
| `TEST-BOX-B` | 200 x 55 x 160 mm | 200 x 160 mm | 80 x 40 mm | 2 |
| `TEST-BOX-C` | 250 x 55 x 160 mm | 250 x 160 mm | 80 x 40 mm | 1 |
| `DEMO-BOX` | 152 x 85 x 110 mm | 152 x 110 mm | 80 x 40 mm | 1 |

The test SKUs have `fixedOrientation: true`. The packer must not rotate them, or
the grip opening no longer faces the arm.

## Manual layout

Coordinates use the packing frame: X left-to-right (0..550), Y from the opening
into the cage (0..250), Z up (0..400), all in millimetres. Every carton is on the
floor; `z = 0`.

| Seq | Carton | X | Y | Z | Target front-face centre |
|---:|---|---:|---:|---:|---|
| 1 | B rear (left) | 20 | 169 | 0 | (120, 169, 80) |
| 2 | B front of B rear | 20 | 112 | 0 | (120, 112, 80) |
| 3 | A rear (left front group) | 20 | 56 | 0 | (120, 56, 60) |
| 4 | A front | 20 | 0 | 0 | (120, 0, 60) |
| 5 | C rear (right) | 260 | 169 | 0 | (385, 169, 80) |
| 6 | DEMO-BOX front (right) | 329 | 81 | 0 | (405, 81, 55) |

The left column is at X=20..220 and the right column at X=260..510, leaving
20 mm on the left side, 40 mm on the right side and 40 mm between columns
inside the 550 mm cage opening. The rear row ends at Y=224, leaving 26 mm to
the back wall. The left depth rows are compressed to 1-2 mm gaps: A front
starts at the opening plane (Y=0), followed by A rear, B front and B rear.
DEMO-BOX is moved in from the opening to Y=81, leaving 3 mm to C at Y=169.

Execution order is rear-to-front, one carton at a time:

`B rear → B front → A rear → A front → C → DEMO-BOX`

The manual plan sets `payloadRadiusM=0` and `targetZOffsetM=0`. The current
sphere-based payload check cannot represent a carton whose bottom rests on the
cage floor; this case therefore uses tool-point path checking. Preview all six
paths and verify carton/gripper clearance visually before executing.

## Checks

- `node --test tests/digitalTwin.test.mjs` verifies the six manual placements,
  fixed orientation, cage bounds and target issues.
- `npm run typecheck` and `npm run build` verify the client.
- Dry-run all six targets in Arm with the taught cage; the default cage pose
  reaches every target.
- Keep the software STOP reachable. Move slowly and do not place a carton until
  the previous one is stable.
