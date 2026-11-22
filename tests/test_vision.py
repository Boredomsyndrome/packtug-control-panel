import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from fastapi import HTTPException

import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from arm_server import (
    _detect_qr_roi,
    _estimate_charuco_camera_pose,
    _estimate_qr_camera_pose,
    _pose_transform,
    _qr_base_pose,
    _qr_diagonal_center,
    _require_stationary_arm,
    _solve_hand_eye,
)


class QrDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sheet = cv2.imread("docs/calibration/qr_labels_a4_55mm_300dpi.png")
        ok, _, points, _ = cv2.QRCodeDetector().detectAndDecodeMulti(sheet)
        if not ok:
            raise RuntimeError("calibration QR sheet could not be decoded")
        qr = points[0]
        x0, y0 = qr.min(axis=0)
        x1, y1 = qr.max(axis=0)
        cls.qr = sheet[
            max(0, int(y0 - 70)) : int(y1 + 70),
            max(0, int(x0 - 70)) : int(x1 + 70),
        ]

    def frame_with_qr(self, size):
        tag = cv2.resize(self.qr, (size, size), interpolation=cv2.INTER_AREA)
        frame = np.full((480, 640, 3), 255, dtype=np.uint8)
        x, y = 320 - size // 2, 240 - size // 2
        frame[y : y + size, x : x + size] = tag
        return frame

    def test_detector_handles_small_and_close_tags_without_fixed_area_limits(self):
        for size in (55, 80, 140, 300):
            result = _detect_qr_roi(self.frame_with_qr(size), 320, 240, 60)
            self.assertTrue(result["ok"], (size, result))
            self.assertLess(abs(result["center"]["x"] - 320), 2)
            self.assertLess(abs(result["center"]["y"] - 240), 2)

    def test_tight_roi_is_padded_and_perspective_center_uses_diagonals(self):
        tag = cv2.resize(self.qr, (180, 180), interpolation=cv2.INTER_AREA)
        source = np.float32([[0, 0], [179, 0], [179, 179], [0, 179]])
        destination = np.float32([[245, 190], [390, 210], [370, 315], [270, 300]])
        transform = cv2.getPerspectiveTransform(source, destination)
        frame = cv2.warpPerspective(tag, transform, (640, 480), borderValue=(255, 255, 255))
        result = _detect_qr_roi(frame, 320, 250, 60, (255, 200, 125, 105))
        self.assertTrue(result["ok"], result)
        corners = np.asarray([[p["x"], p["y"]] for p in result["corners"]])
        expected = _qr_diagonal_center(corners)
        self.assertAlmostEqual(result["center"]["x"], float(expected[0]), places=1)
        self.assertAlmostEqual(result["center"]["y"], float(expected[1]), places=1)
        self.assertGreater(abs(result["angle_deg"]), 2)

    def test_angle_tracks_the_printed_qr_top_edge(self):
        tag = cv2.resize(self.qr, (180, 180), interpolation=cv2.INTER_AREA)
        for turns, expected in ((0, 0), (1, -90), (2, -180), (3, 90)):
            frame = np.full((480, 640, 3), 255, dtype=np.uint8)
            frame[150:330, 230:410] = np.rot90(tag, turns)
            result = _detect_qr_roi(frame, 320, 240, 80, (220, 140, 200, 200))
            self.assertTrue(result["ok"], result)
            self.assertAlmostEqual(result["angle_deg"], expected, delta=1)


class EyeInHandTests(unittest.TestCase):
    def test_generated_charuco_board_is_detectable(self):
        image = cv2.imread("docs/calibration/charuco_5x7_30mm_a4_300dpi.png")
        h, w = image.shape[:2]
        camera = np.asarray([[2500, 0, w / 2], [0, 2500, h / 2], [0, 0, 1]])
        with patch(
            "arm_server._camera_intrinsics",
            return_value=(camera, np.zeros((5, 1))),
        ):
            result = _estimate_charuco_camera_pose(image)
        self.assertGreaterEqual(result["corners"], 20)
        self.assertLess(result["reprojection_error_px"], 1)

    def test_known_qr_size_recovers_camera_pose(self):
        half = 0.035 / 2
        object_points = np.asarray(
            [[-half, half, 0], [half, half, 0], [half, -half, 0], [-half, -half, 0]],
            dtype=np.float64,
        )
        camera = np.asarray([[610, 0, 320], [0, 608, 240], [0, 0, 1]], dtype=np.float64)
        distortion = np.zeros((5, 1), dtype=np.float64)
        expected_rvec = np.asarray([0.2, -0.3, 0.1], dtype=np.float64)
        expected_tvec = np.asarray([0.01, -0.02, 0.4], dtype=np.float64)
        image_points, _ = cv2.projectPoints(
            object_points, expected_rvec, expected_tvec, camera, distortion
        )
        corners = [
            {"x": float(x), "y": float(y)} for x, y in image_points.reshape(4, 2)
        ]
        with patch(
            "arm_server._camera_intrinsics", return_value=(camera, distortion)
        ):
            result = _estimate_qr_camera_pose(corners, 640, 480)
        np.testing.assert_allclose(result["rvec"], expected_rvec, atol=1e-6)
        np.testing.assert_allclose(result["tvec"], expected_tvec, atol=1e-6)
        self.assertLess(result["reprojection_error_px"], 0.001)

    def test_hand_eye_recovers_camera_to_tool_and_base_target(self):
        camera_to_tool = np.eye(4)
        camera_to_tool[:3, :3] = cv2.Rodrigues(np.asarray([0.2, -0.1, 0.15]))[0]
        camera_to_tool[:3, 3] = [0.04, -0.03, 0.08]
        base_target = np.eye(4)
        base_target[:3, :3] = cv2.Rodrigues(np.asarray([0.1, 0.2, -0.05]))[0]
        base_target[:3, 3] = [0.45, 0.1, 0.08]
        samples = []
        for index in range(12):
            arm_pose = {
                "x": 0.25 + 0.01 * index,
                "y": -0.15 + 0.025 * index,
                "z": 0.18 + 0.01 * (index % 4),
                "roll": -35 + 7 * index,
                "pitch": -20 + 5 * (index % 7),
                "yaw": -50 + 11 * index,
            }
            camera_target = (
                np.linalg.inv(camera_to_tool)
                @ np.linalg.inv(_pose_transform(arm_pose))
                @ base_target
            )
            camera_rvec = cv2.Rodrigues(camera_target[:3, :3])[0].reshape(3)
            samples.append(
                {
                    "arm_pose": arm_pose,
                    "camera_rvec": camera_rvec.tolist(),
                    "camera_tvec": camera_target[:3, 3].tolist(),
                }
            )
        solved = _solve_hand_eye(samples)
        np.testing.assert_allclose(solved["rotation"], camera_to_tool[:3, :3], atol=1e-8)
        np.testing.assert_allclose(
            solved["translation"], camera_to_tool[:3, 3], atol=1e-8
        )
        mapped = _qr_base_pose(
            samples[3]["arm_pose"],
            {
                "rvec": samples[3]["camera_rvec"],
                "tvec": samples[3]["camera_tvec"],
            },
            solved,
        )
        np.testing.assert_allclose(
            [mapped["x"], mapped["y"], mapped["z"]], base_target[:3, 3], atol=1e-6
        )
        offset = (0.01, 0.0525, -0.005)
        mapped_target = _qr_base_pose(
            samples[3]["arm_pose"],
            {
                "rvec": samples[3]["camera_rvec"],
                "tvec": samples[3]["camera_tvec"],
            },
            solved,
            offset,
        )
        expected_target = base_target @ np.asarray([*offset, 1.0])
        np.testing.assert_allclose(
            [mapped_target["x"], mapped_target["y"], mapped_target["z"]],
            expected_target[:3],
            atol=1e-6,
        )

    def test_frame_pose_pair_is_rejected_if_arm_moves(self):
        captured = {"x": 0.3, "y": 0.0, "z": 0.2, "roll": 0, "pitch": 0, "yaw": 0}
        controller = Mock()
        controller.status.return_value = {
            "connected": True,
            "busy": False,
            "pose": {**captured, "x": captured["x"] + 0.004},
        }
        with self.assertRaises(HTTPException):
            _require_stationary_arm(controller, captured)


if __name__ == "__main__":
    unittest.main()
