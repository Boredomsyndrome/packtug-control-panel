import unittest
import contextlib
from unittest.mock import Mock, patch
from fastapi import HTTPException

from server.arm_server import (
    ArmController, CageIn, ExecutePickIn, PickPathIn, PoseIn,
    _cage_to_world, plan_pick_path,
)


class PickTargetTests(unittest.TestCase):
    def pose(self, cage, local):
        x, y, z = _cage_to_world(cage, local)
        return PoseIn(x=x, y=y, z=z, roll=180, pitch=0, yaw=0)

    def test_demo_targets_enter_through_gate(self):
        for yaw in (0, 90, -90, 37, 180):
            cage = CageIn(x=0, y=0, z=0.02, yaw=yaw)
            for local in ((-.080, .18, .0875), (.081, .18, .0875), (0, .18, .1975), (-.080, .085, .0875), (.081, .085, .0875)):
                plan = plan_pick_path(PickPathIn(
                    start=self.pose(cage, (local[0], -.08, local[2])),
                    target=self.pose(cage, local), cage=cage,
                ), None, .6)
                self.assertTrue(plan['ok'], plan)
                self.assertIn('gate', [point['kind'] for point in plan['points']])
                for point in plan['points'][1:]:
                    self.assertEqual(point['pose']['roll'], 180)
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
        target = self.pose(cage, (0, .18, .0875)).model_copy(update={'roll': 30, 'pitch': 45})
        for drift in (False, True):
            arm = ArmController(mock=True)
            arm._check_motion_locked = Mock()
            arm.tool = 'GRIPPER_1'
            arm.pose = start.model_dump()
            arm.set_velocity = Mock()
            arm._gripper_action = Mock()
            def move(x, y, z, roll, pitch, yaw, **kwargs):
                if kwargs.get('strict_linear'):
                    self.assertEqual((roll, pitch, yaw), (180, 0, 90))
                arm.pose = dict(x=x, y=y, z=z, roll=roll, pitch=pitch, yaw=yaw)
                if drift and abs(y - target.y) < 1e-6:
                    arm.pose['pitch'] = 8
                return arm.snapshot()
            arm.move_pose_deg = Mock(side_effect=move)
            retract = ([{'kind': 'start', 'pose': start.model_dump()}], [], True, [])
            with patch('server.arm_server._plan_retract_path', return_value=retract):
                result = arm.execute_pick_place(ExecutePickIn(
                    target=target, home=start, cage=cage, return_home_after=True,
                ))
            self.assertEqual(result['ok'], not drift, result)
            releases = [c for c in arm._gripper_action.call_args_list if c.args == (False,)]
            self.assertEqual(len(releases), 0 if drift else 1)
            if not drift:
                self.assertEqual((arm.pose['roll'], arm.pose['pitch'], arm.pose['yaw']), (180, 0, 90))
                self.assertAlmostEqual(arm.pose['y'], start.y)

    def test_taught_final_overrides_requested_coordinates_and_attitude(self):
        from server.arm_server import _taught_final_pose
        taught = _taught_final_pose()
        cage = CageIn(x=.2, y=-.1, z=0)
        request = PickPathIn(
            start=PoseIn(x=.2, y=-.2, z=.1, roll=0, pitch=0, yaw=0),
            target=PoseIn(x=.3, y=0, z=.2, roll=180, pitch=0, yaw=90),
            cage=cage, use_taught_final=True,
        )
        plan = plan_pick_path(request, None, .47)
        self.assertEqual(plan['points'][-1]['pose'], taught)
        for point in plan['points'][1:]:
            for key in ('roll', 'pitch', 'yaw'):
                self.assertEqual(point['pose'][key], taught[key])

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


if __name__ == '__main__':
    unittest.main()
