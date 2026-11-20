#!/usr/bin/env python3
"""Generate the A4 ChArUco board used by wrist-camera calibration.

Print the PNG at 100% / actual size. The 5x7 board must measure exactly
150 x 210 mm; do not use "fit to page".
"""

from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


DPI = 300
A4_MM = (210, 297)
SQUARES = (5, 7)
SQUARE_MM = 30
MARKER_MM = 22
OUTPUT = Path(__file__).with_name("charuco_5x7_30mm_a4_300dpi.png")


def px(mm: float) -> int:
    return round(mm / 25.4 * DPI)


dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
board = cv2.aruco.CharucoBoard(
    SQUARES,
    SQUARE_MM / 1000,
    MARKER_MM / 1000,
    dictionary,
)
board_width = px(SQUARES[0] * SQUARE_MM)
board_height = px(SQUARES[1] * SQUARE_MM)
board_image = board.generateImage((board_width, board_height), marginSize=0, borderBits=1)
page = np.full((px(A4_MM[1]), px(A4_MM[0])), 255, dtype=np.uint8)
x = (page.shape[1] - board_width) // 2
y = (page.shape[0] - board_height) // 2
page[y : y + board_height, x : x + board_width] = board_image
ok, encoded = cv2.imencode(".png", page)
if not ok:
    raise RuntimeError("could not encode ChArUco board")
Image.open(BytesIO(encoded.tobytes())).save(OUTPUT, dpi=(DPI, DPI))
print(OUTPUT)
