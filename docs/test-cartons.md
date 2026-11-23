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
| 1 | B rear (left) | 20 | 174 | 0 | (120, 174, 80) |
| 2 | B front of B rear | 20 | 116 | 0 | (120, 116, 80) |
| 3 | A rear (left front group) | 20 | 58 | 0 | (120, 58, 60) |
| 4 | A front | 20 | 1 | 0 | (120, 1, 60) |
| 5 | C rear (right) | 280 | 174 | 0 | (405, 174, 80) |
| 6 | DEMO-BOX front (right) | 329 | 1 | 0 | (405, 1, 55) |

The left column is at X=20..220 and the right column at X=280..530, leaving
20 mm inside each side of the 550 mm cage opening. The depth rows are separated
by 2-3 mm; the rear row ends at Y=229, leaving 21 mm to the back wall. The front
faces start at Y=1 mm so they are just inside the opening plane. This pulls the
rear cartons about 16 mm outward compared with the first layout so the box back
does not press the cage rear wall.

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
