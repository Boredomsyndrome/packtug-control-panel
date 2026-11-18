# Demo cargo set for 3D printing

A compact physical cargo set for the arm/cage demo. It is intentionally smaller
than the mock SKUs in `src/data/mock.ts` so the standard gripper can pick every
box and the arm can place it without touching adjacent boxes.

## Assumptions

- Cage: `550 W x 250 D x 400 H mm`, 20 kg limit (`CAGE_BIN`).
- Arm: 6-axis Ned, `470 mm` max horizontal reach, `300 g` payload.
- Gripper: Niryo **Custom/Standard Gripper**, max opening **43 mm**.
- Current tool ID reported by the bridge: `GRIPPER_1`.
- All boxes are picked from the front, placed bottom-up.
- Do not use the existing large mock SKUs for the physical demo: their
  `120-250 mm` widths are not gripper-compatible.

## Print set

| ID | W x D x H (mm) | Qty | Target weight | Colour |
|---|---:|---:|---:|---|
| `CRG-S` | **100 x 34 x 40** | 3 | 45-60 g | grey |
| `CRG-M` | **100 x 34 x 60** | 3 | 65-85 g | orange |
| `CRG-L` | **100 x 34 x 80** | 3 | 85-110 g | blue |

Total: **9 boxes**, approximately 0.6-0.8 kg, 3 layers in 3 columns.

The `34 mm` dimension is the **grip depth**. It leaves `43 - 34 = 9 mm` total
grip opening clearance (4.5 mm per side), and still gives a stable 100 mm long
box. The 100 mm faces are the large grip faces; keep them flat and free of raised
labels/ribs.

## Cage layout

Coordinates: `X` = cage width, `Y` = depth from front opening, `Z` = height.
Cage origin is the front-left floor corner.

| Stack | Box | X (mm) | Y (mm) | Z (mm) | Column top |
|---|---|---:|---:|---|---:|
| A | `CRG-S` | 30 | 25 | 0, 40, 80 | 120 mm |
| B | `CRG-M` | 220 | 25 | 0, 60, 120 | 180 mm |
| C | `CRG-L` | 410 | 25 | 0, 80, 160 | 240 mm |

Clearances:

- Box-to-box gap: **90 mm** (`220 - (30+100)` and `410 - (220+100)`).
- Gripper body is about **77 mm** wide, so this leaves about **6.5 mm per side**.
- Left wall: 30 mm; right wall: 40 mm; front: 25 mm.
- Top of highest stack: 240 mm; cage top clearance: **160 mm**.
- Every box sits fully on the floor or on the flat top of the box below.

```text
Cage front (Y=0)                         Cage back (Y=250)
        X=30          X=220         X=410
       +------+      +------+      +------+
Z=240  |      |      |      |      |  L   |
Z=160  |      |      |      |      |  L   |
Z=120  |      |      |  M   |      |  L   |
Z=80   |  S   |      |  M   |      |      |
Z=40   |  S   |      |  M   |      |      |
Z=0    +------+      +------+      +------+
        100x34        100x34        100x34
```

Place bottom-up: A0, B0, C0, then the next layer. Do not reach over a completed
stack.

## Print settings

- Material: PLA or PETG.
- Nozzle: 0.4 mm.
- Layer: 0.2 mm.
- Walls: 4 perimeters (~1.6 mm).
- Top/bottom: 4 layers (~0.8-1.2 mm); 10-15% gyroid infill.
- 1 mm chamfer on top and bottom edges; no deep interlocking lip.
- No supports. Print upright on the 100 x 34 mm base.
- Closed box, flat top: **no raised lid, clasp, rib or label in the grip area**.
- Put a 0.4 mm debossed label on the top or back, never on the two 100 x H grip
  faces.
- Target outer grip depth: **34.0 mm** (accept 33.8-34.4 mm). If a test print is
  wider than 35 mm, reprint or sand the grip faces.

## Jam/collision checks before printing the batch

1. Print one `CRG-S` first; measure the 34 mm grip depth.
2. Open/close the gripper and confirm it closes around the 100 x H faces without
   touching the box corners.
3. Pick and place the test box at `X=30, Y=25, Z=0`; then place a second one at
   `Z=40`.
4. Check that the gripper body clears the neighbouring column; the 90 mm gap is
   based on the 77 mm gripper body.
5. Place the `CRG-L` stack to `Z=160` (top 240 mm) and verify the arm/wrist has
   clearance below the 400 mm cage top.
6. Only then print the remaining eight boxes.

If the physical gripper is the **Large Gripper** (79 mm opening) instead of the
standard 43 mm gripper, the same set still works; it can optionally be scaled up
later. If the gripper opening measures below 40 mm, reduce the grip depth to
30-32 mm before printing the batch.
