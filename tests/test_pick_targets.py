import unittest
import contextlib
from types import SimpleNamespace
from unittest.mock import Mock, patch
from fastapi import HTTPException

import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from arm_server import (
    ArmController, CageIn, ExecutePickIn, PickPathIn, PoseIn,
    _cage_to_world, _verify_level_attitude, _verify_placement_attitude,
    decode_hardware_faults, plan_pick_path,
)


class PickTargetTests(unittest.TestCase):
    def pose(self, cage, local):
        x, y, z = _cage_to_world(cage, local)
        return PoseIn(x=x, y=y, z=z, roll=180, pitch=0, yaw=0)

    def test_demo_targets_enter_through_gate(self):
        for yaw in (0, 90, -90, 37, 180):
            cage = CageIn(x=.3, y=0, z=0.02, yaw=yaw)
            for local in ((-.080, .160, .055), (.081, .160, .055), (0, .160, .165), (-.080, .065, .055), (.081, .065, .055)):
                plan = plan_pick_path(PickPathIn(
                    start=self.pose(cage, (local[0], -.08, local[2])),
                    target=self.pose(cage, local), cage=cage,
                ), None, .6)
                self.assertTrue(plan['ok'], plan)
                self.assertIn('gate', [point['kind'] for point in plan['points']])
                for point in plan['points'][1:]:
                    self.assertEqual(point['pose']['roll'], 0)
                    self.assertEqual(point['pose']['pitch'], 0)
                    self.assertAlmostEqual((point['pose']['yaw'] - yaw - 90) % 360, 0)

    def test_invalid_targets_block_preview_and_execution_before_motion(self):
        cage = CageIn(x=0, y=0, z=.02)
        start = self.pose(cage, (0, -.1, .1))
        for local in ((0, .27, .1), (.3, .1, .1), (0, .1, .45), (0, .23, .1)):
            target = self.pose(cage, local)
            plan = plan_pick_path(PickPathIn(start=start, target=target, cage=cage), None, .6)
            self.assertFalse(plan['ok'])
            self.assertTrue(next(p for p in plan['points'] if p['kind'] == 'target')['issues'])
            arm = ArmController(mock=True)
            arm._check_motion_locked = Mock()
            arm.tool = 'GRIPPER_1'
            arm.pose = start.model_dump()
            arm.set_velocity = Mock()
            arm._gripper_action = Mock()
            arm.move_pose_deg = Mock()
            result = arm.execute_pick_place(ExecutePickIn(target=target, home=start, cage=cage))
            self.assertEqual(result['stage'], 'target_check', result)
            arm.set_velocity.assert_not_called()
            arm._gripper_action.assert_not_called()
            arm.move_pose_deg.assert_not_called()

    def test_tilted_input_levels_before_entry_and_only_releases_when_verified(self):
        cage = CageIn(x=.2, y=0, z=.02)
        start = PoseIn(x=.2, y=-.1, z=.1, roll=140, pitch=25, yaw=12)
        target = self.pose(cage, (0, .18, .055)).model_copy(update={'roll': 30, 'pitch': 45})
        for drift in (False, True):
            arm = ArmController(mock=True)
            arm._check_motion_locked = Mock()
            arm.tool = 'GRIPPER_1'
            arm.pose = start.model_dump()
            arm.set_velocity = Mock()
            arm._gripper_action = Mock()
            def move(x, y, z, roll, pitch, yaw, **kwargs):
                if kwargs.get('strict_linear'):
                    # The path/return moves run after the retract-point
                    # orientation, so they always use the cage-aligned yaw.
                    self.assertEqual((roll, pitch, yaw), (0, 0, 90))
                arm.pose = dict(x=x, y=y, z=z, roll=roll, pitch=pitch, yaw=yaw)
                if drift and abs(y - target.y) < 1e-6:
                    arm.pose['pitch'] = 8
                return arm.snapshot()
            arm.move_pose_deg = Mock(side_effect=move)
            retract = ([{'kind': 'start', 'pose': start.model_dump()}], [], True, [])
            with patch('arm_server._plan_retract_path', return_value=retract):
                result = arm.execute_pick_place(ExecutePickIn(
                    target=target, home=start, cage=cage, return_home_after=True,
                ))
            self.assertEqual(result['ok'], not drift, result)
            releases = [c for c in arm._gripper_action.call_args_list if c.args == (False,)]
            self.assertEqual(len(releases), 0 if drift else 1)
            if not drift:
                self.assertEqual((arm.pose['roll'], arm.pose['pitch'], arm.pose['yaw']), (0, 0, 90))
                self.assertAlmostEqual(arm.pose['y'], start.y)

    def test_requested_front_face_target_is_kept(self):
        cage = CageIn(x=.2, y=-.1, z=0)
        target = PoseIn(x=.3, y=0, z=.2, roll=0, pitch=0, yaw=90)
        request = PickPathIn(
            start=PoseIn(x=.2, y=-.2, z=.1, roll=0, pitch=0, yaw=0),
            target=target,
            cage=cage,
        )
        plan = plan_pick_path(request, None, .47)
        self.assertEqual(plan['points'][-1]['pose'], target.model_dump())
        for point in plan['points'][1:]:
            self.assertEqual(point['pose']['roll'], 0)
            self.assertEqual(point['pose']['pitch'], 0)
            self.assertEqual(point['pose']['yaw'], 90)

    def test_linear_abort_cannot_fallback_during_level_placement(self):
        arm = ArmController(mock=False)
        arm._check_motion_locked = Mock()
        arm._robot = Mock()
        arm.pose = dict(x=.2, y=0, z=.2, roll=120, pitch=20, yaw=10)
        arm._socket_timeout = lambda *_: contextlib.nullcontext()
        arm._send_linear_pose = Mock(side_effect=RuntimeError('Goal has been aborted'))
        arm._query_collision_flag_locked = Mock(return_value=False)
        with self.assertLogs(level='ERROR'):
            with self.assertRaises(HTTPException):
                arm.move_pose_deg(.25, 0, .2, 180, 0, 0, strict_linear=True)
        arm._robot.inverse_kinematics.assert_not_called()
        arm._robot.move_joints.assert_not_called()

    def test_outer_retract_allows_yaw_error_but_gate_does_not(self):
        target = dict(x=.2, y=0, z=.2, roll=0, pitch=0, yaw=0)
        # This matches the hardware reading from the failed run: the gripper
        # is level, but J5 is near its limit and yaw is still 4.3 degrees away.
        actual = dict(x=.2, y=-.2, z=.2, roll=-.3, pitch=.1, yaw=-4.3)
        _verify_level_attitude(actual, target)
        with self.assertRaises(HTTPException) as caught:
            _verify_placement_attitude(actual, target)
        self.assertIn("actual R/P/Y -0.3/0.1/-4.3", caught.exception.detail)
        self.assertIn("error 0.3/0.1/4.3", caught.exception.detail)

    def test_pre_gate_near_base_blocks_before_grasp(self):
        cage = CageIn(x=.081, y=-.038, z=.02, yaw=-94.3)
        start = PoseIn(x=-.1, y=-.25, z=.2, roll=0, pitch=0, yaw=-48.4)
        target = self.pose(cage, (0, .12, .18))
        plan = plan_pick_path(PickPathIn(start=start, target=target, cage=cage), None, .47)
        pre_gate = next(point for point in plan['points'] if point['kind'] == 'pre_gate')
        self.assertLess(pre_gate['reach_m'], .18)
        self.assertFalse(plan['ok'])
        self.assertTrue(any('base exclusion zone' in issue for issue in pre_gate['issues']))
        arm = ArmController(mock=True)
        arm._check_motion_locked = Mock()
        arm.tool = 'GRIPPER_1'
        arm.pose = start.model_dump()
        arm.set_velocity = Mock()
        arm._gripper_action = Mock()
        arm.move_pose_deg = Mock()
        result = arm.execute_pick_place(ExecutePickIn(target=target, home=start, cage=cage))
        self.assertEqual(result['stage'], 'pre_gate_check', result)
        arm.set_velocity.assert_not_called()
        arm._gripper_action.assert_not_called()
        arm.move_pose_deg.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class LinearModeTests(unittest.TestCase):
    def test_linear_offset_keeps_current_attitude(self):
        arm = ArmController(mock=True)
        arm.connected = True
        arm.pose = {
            "x": 0.30, "y": 0.02, "z": 0.20,
            "roll": -6.0, "pitch": 4.0, "yaw": 12.0,
        }
        result = arm.move_linear(0.01, -0.02, 0.05)
        pose = result["pose"]
        self.assertAlmostEqual(pose["x"], 0.31)
        self.assertAlmostEqual(pose["y"], 0.00)
        self.assertAlmostEqual(pose["z"], 0.25)
        self.assertEqual(pose["roll"], -6.0)
        self.assertEqual(pose["pitch"], 4.0)
        self.assertEqual(pose["yaw"], 12.0)

    def test_linear_yaw_offset_keeps_roll_and_pitch(self):
        arm = ArmController(mock=True)
        arm.connected = True
        arm.pose = {
            "x": 0.30, "y": 0.02, "z": 0.20,
            "roll": 180.0, "pitch": 0.0, "yaw": 170.0,
        }
        result = arm.move_linear(0.0, 0.0, 0.0, 20.0)
        pose = result["pose"]
        self.assertEqual(pose["roll"], 180.0)
        self.assertEqual(pose["pitch"], 0.0)
        self.assertEqual(pose["yaw"], -170.0)

    def test_linear_pitch_offset_keeps_roll(self):
        arm = ArmController(mock=True)
        arm.connected = True
        arm.pose = {
            "x": 0.30, "y": 0.02, "z": 0.20,
            "roll": 180.0, "pitch": 10.0, "yaw": 90.0,
        }
        result = arm.move_linear(0.0, 0.0, 0.0, 0.0, 15.0)
        pose = result["pose"]
        self.assertEqual(pose["roll"], 180.0)
        self.assertEqual(pose["pitch"], 25.0)
        self.assertEqual(pose["yaw"], 90.0)

    def test_jog_pose_streams_offset_without_planning(self):
        arm = ArmController(mock=True)
        arm.connected = True
        arm.pose = {
            "x": 0.30, "y": 0.02, "z": 0.20,
            "roll": 180.0, "pitch": 5.0, "yaw": 10.0,
        }
        arm.joints_deg = [0.0, 10.0, -20.0, 0.0, 30.0, 0.0]
        result = arm.jog_pose(0.01, -0.01, 0.02, 2.0, 3.0)
        pose = result["pose"]
        self.assertAlmostEqual(pose["x"], 0.31)
        self.assertAlmostEqual(pose["y"], 0.01)
        self.assertAlmostEqual(pose["z"], 0.22)
        self.assertAlmostEqual((pose["roll"] - 180.0) % 360.0, 0.0)
        self.assertAlmostEqual(pose["pitch"], 7.0)
        self.assertAlmostEqual(pose["yaw"], 13.0)

    def test_zero_linear_offset_does_not_move(self):
        arm = ArmController(mock=True)
        arm.connected = True
        arm.pose = {
            "x": 0.30, "y": 0.02, "z": 0.20,
            "roll": 180.0, "pitch": 0.0, "yaw": 90.0,
        }
        result = arm.move_linear(0.0, 0.0, 0.0)
        self.assertEqual(result["pose"], arm.pose)


class HardwareFaultTests(unittest.TestCase):
    def test_decode_j5_overload_and_tool_voltage(self):
        faults, summary = decode_hardware_faults(
            [0, 0, 0, 0, 32, 0, 1],
            ["joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6", "Tool"],
        )
        self.assertEqual([fault["joint"] for fault in faults], ["J5", "Tool"])
        self.assertEqual(faults[0]["code"], 32)
        self.assertEqual(faults[0]["reasons"], ["overload"])
        self.assertIn("J5 overload (code 32)", summary)
        self.assertIn("Tool input voltage (code 1)", summary)

    def test_hardware_fault_blocks_motion(self):
        arm = ArmController(mock=True)
        arm.connected = True
        arm.calibration_needed = False
        arm.learning_mode = False
        arm.hardware_fault = "J5 overload (code 32)"
        arm.hardware_motion_fault = "J5 overload (code 32)"
        with self.assertRaises(HTTPException) as caught:
            arm.move_joint(5, 5.0)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("hardware fault", caught.exception.detail)

    def test_no_error_sentinel_is_not_a_fault(self):
        arm = ArmController(mock=True)
        arm._store_hardware_locked(SimpleNamespace(
            motor_names=[f"joint_{i}" for i in range(1, 7)],
            motor_types=[],
            motors_temperature=[40.0] * 6,
            motors_voltage=[11.0] * 6,
            hardware_errors=[0] * 6,
            error_message="''",
        ))
        self.assertIsNone(arm.hardware_fault)
        self.assertIsNone(arm.hardware_motion_fault)
        self.assertIsNone(arm.hardware_tool_fault)
        self.assertEqual(arm.hardware_faults, [])

    def test_tool_voltage_warning_does_not_block_motion(self):
        arm = ArmController(mock=True)
        arm.connected = True
        arm.calibration_needed = False
        arm.learning_mode = False
        arm.joints_deg = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        arm.hardware_fault = "Tool input voltage (code 1)"
        arm.hardware_tool_fault = "Tool input voltage (code 1)"
        self.assertIsNone(arm.hardware_motion_fault)
        arm._check_motion_locked()
