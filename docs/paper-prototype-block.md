# Hand-made paper block (temporary prototype)

Current measured block:

**L x W x H = 152 x 85 x 110 mm** (15.2 x 8.5 x 11 cm).

Because the grip opening must be centered horizontally, use the **152 x 85 mm
face as the floor** and the **152 x 110 mm face as the arm/camera-facing front**.
Height is 110 mm.

## Front-face layout

The QR code itself is now **35 x 35 mm**. The printed label needs the normal
quiet zone, so its physical size is **35 + 10 + 10 = 55 x 55 mm**. The 80 x 40
mm grip opening and 55 mm label fit if the hole is placed near one end; if the
hole is vertically centered, only 35 mm remains above and below it, and the
55 mm label will **not** fit on the front face.

If the hole is already centered, put the 55 mm label on the **top face**
(152 x 85 mm) or the side face instead of shrinking the code further. The QR
payload does **not** change.

For a hole placed high on the 110 mm face, the layout is:

```text
top margin           2.5 mm
grip opening        40.0 mm
gap                  5.0 mm
QR label (35 code)  55.0 mm
bottom margin        7.5 mm
-----------------------------
total              110.0 mm
```

- QR: **35 mm code + 10 mm quiet zone = 55 x 55 mm**, centered horizontally.
- Grip opening: **80 x 40 mm** (10 mm central web, 35 mm side slots), centered.
- Side margins beside the 80 mm opening: **36 mm** each.
- Hole center height from the box bottom: **87.5 mm**.
- QR center height from the box bottom: **35.0 mm**.

Keep the QR quiet zone clear and reinforce the paper around the QR and the
central web. Do not put the QR over the hole. Three blocks can be stacked 2-high
(220 mm); 3-high is 330 mm and leaves 70 mm below the cage top.

The paper box is only a fit/visibility prototype. Reinforce the QR area and
the 10 mm central web with cardboard; a paper-only web will flex or tear under
gripper load.
