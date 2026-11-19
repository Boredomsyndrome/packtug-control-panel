# Five-box stack demo

Box: `152 W x 85 D x 110 H mm` (measured 15.2 x 8.5 x 11 cm), paper/printed
demo block, 0.2 kg target weight. Cage: `550 W x 250 D x 400 H mm`. Cage frame:
X to the right, Y into the cage, Z up; all dimensions in mm.

The front face is `152 x 110 mm`. The QR code is **35 x 35 mm**, so the printed
label is **55 x 55 mm with the 10 mm quiet zone**. It fits above/below the
`80 x 40 mm` grip opening only if the opening is off-center; with a vertically
centered hole, place the label on the top or side face. The QR payload does
not change (see `paper-prototype-block.md`).

## Stack layout

Four boxes on the floor in a 2 x 2 grid, pushed toward the back wall, plus one
box centered on the back row. This keeps the centre of gravity away from the
cage opening and makes the demo less likely to topple while the cart moves.

| # | Item | X | Y | Z (bottom) | Top Z |
|---|---|---:|---:|---:|---:|
| 1 | back left | 119 | 160 | 0 | 110 |
| 2 | back right | 280 | 160 | 0 | 110 |
| 3 | top (back-center) | 199 | 160 | 110 | 220 |
| 4 | front left | 119 | 65 | 0 | 110 |
| 5 | front right | 280 | 65 | 0 | 110 |

Box centers:

| # | Center X | Center Y | Center Z |
|---|---:|---:|---:|
| 1 | 195 | 202.5 | 55 |
| 2 | 356 | 202.5 | 55 |
| 3 | 275 | 202.5 | 165 |
| 4 | 195 | 107.5 | 55 |
| 5 | 356 | 107.5 | 55 |

The top box bridges the **9 mm gap** between the two back base boxes; its
support is about 94% of its footprint. It is centered on the back row, not
hanging over the opening.

## Arm placement target

For digital-twin pick/place planning, assume the gripper end is at the
**centre of the carton front face**, at mid-height. For a placement
`(x, y, z, w, d, h)`, the packing-frame target is:

```
(x + w/2, y, z + h/2)
```

The software keeps the gripper end horizontal (`roll 180`, `pitch 0`,
`yaw = cage yaw + 90`). The physical demo block still has its gripper web at
the hole centre; the front-face target is the planning convention and may
need a fixture or TCP offset for the real tool.

| Place | Target X | Target Y | Target Z |
|---|---:|---:|---:|
| back left | 195 | 160 | 55 |
| back right | 356 | 160 | 55 |
| top (back-center) | 275 | 160 | 165 |
| front left | 195 | 65 | 55 |
| front right | 356 | 65 | 55 |

The target Y is the front face plane; the carton extends from that plane into
the cage. A safe outside approach starts at `Y = -60 mm` at the same X/Z, then
moves to the target Y. Release by opening the gripper, then retract to
`Y = -60 mm`. Use a slow approach and keep the gripper/jaws horizontal.

These are **cage-frame Cartesian targets**, not arm joint angles. Apply the
camera-to-tool/arm transform and IK before sending them to the physical arm.

## Placement order

1. back left
2. back right
3. top (back-center)
4. front left
5. front right

Placing the top before the front row avoids reaching over a completed front
row. Total height: 220 mm, leaving 180 mm below the cage top. Total load about
1.0 kg, well below the 20 kg cage limit and with each box under the 300 g arm
payload target.
