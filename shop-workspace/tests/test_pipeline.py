import unittest
from unittest.mock import Mock
import numpy as np
from xarm_grasp.coordinates import pose_matrix, camera_to_tcp, tcp_to_base, depth_at, deproject, transform
from xarm_grasp.motion import make_tool_axis_plan_from_tcp
from xarm_grasp.robot import Robot, checked
from xarm_grasp.config import load, validate_common_execution

MOTION = {'lateral_axis': 'y', 'vertical_axis': 'x', 'depth_axis': 'z', 'approach_axis_sign': 1, 'lift_axis_sign': -1, 'lift_mm': 20, 'axis_deadband_mm': 1, 'max_alignment_mm': 300,
          'min_approach_mm': 20, 'max_approach_mm': 600,
          'vertical_axis_min_up_component': 0.7,
          'position_tolerance_mm': 2, 'orientation_tolerance_deg': 0.5}

class GeometryTests(unittest.TestCase):
    def test_camera_to_base_removes_tcp_offset(self):
        point_tcp = camera_to_tcp([10, 0, 500], [0, 0, 100, 0, 0, 0],
                                  pose_matrix([20, 0, 0, 0, 0, 0]))
        actual = tcp_to_base(point_tcp, [300, 20, 300, 0, 0, 90])
        np.testing.assert_allclose(actual, [300, 50, 700], atol=1e-8)

    def test_deprojection_and_depth_rejection(self):
        np.testing.assert_allclose(deproject([420, 240], 500, [500, 500, 320, 240]), [100, 0, 500])
        d = np.full((20, 20), 500.)
        self.assertEqual(depth_at(d, [10, 10]), 500)
        d[7:14, 7:14] = 0
        with self.assertRaises(ValueError): depth_at(d, [10, 10])
        d[:] = 500; d[:, 10:] = 1000
        with self.assertRaises(ValueError): depth_at(d, [10, 10])

    def test_tool_axis_plan_keeps_orientation_and_order(self):
        tcp = [300, 0, 200, 0, 90, 0]
        point = [30, 20, 100]
        plan = make_tool_axis_plan_from_tcp(point, tcp, 30, MOTION, [[0, 700], [-500, 500], [0, 600]])
        self.assertEqual(plan['axis_roles'], {'lateral': 'y', 'vertical': 'x', 'depth': 'z'})
        self.assertEqual(plan['axis_signs'], {'approach': 1, 'lift': -1})
        self.assertEqual([s['name'] for s in plan['steps']],
                         ['align_lateral', 'align_vertical', 'approach_depth', 'lift', 'pullout'])
        np.testing.assert_allclose(plan['target_delta_tool_mm'], [30, 20, 130])
        for step in plan['steps']:
            np.testing.assert_allclose(step['target_pose'][3:], tcp[3:])
        np.testing.assert_allclose(plan['steps'][-1]['target_pose'][:3], [300, 20, 190], atol=1e-8)
    def test_tool_axis_plan_rejects_bad_geometry(self):
        with self.assertRaisesRegex(ValueError, 'tool X- lift direction'):
            bad_pose = [300, 0, 200, 0, 0, 0]
            point = [30, 0, 100]
            make_tool_axis_plan_from_tcp(point, bad_pose, 30, MOTION,
                                [[0, 700], [-500, 500], [0, 600]])
        good_pose = [300, 0, 200, 0, 90, 0]
        behind = [0, 0, -100]
        with self.assertRaisesRegex(ValueError, 'Z\\+'):
            make_tool_axis_plan_from_tcp(behind, good_pose, 30, MOTION,
                                [[0, 700], [-500, 500], [0, 600]])
        bad = np.eye(4); bad[0, 0] = -1
        with self.assertRaises(ValueError):
            transform(bad)
    def test_placeholder_config_rejects_motion(self):
        from pathlib import Path
        c = load(Path(__file__).resolve().parents[1] / 'config.json')
        c['motion_enabled'] = False
        with self.assertRaises(ValueError): validate_common_execution(c)
        c['motion_enabled'] = True
        c['workspace_mm'] = None
        with self.assertRaises(ValueError): validate_common_execution(c)

    def test_tool_move_commands_translation_only(self):
        r = Robot({'tcp_speed_mm_s': 20, 'tcp_acc_mm_s2': 100}); r.arm = Mock()
        r.arm.get_err_warn_code.return_value = (0, [0, 0]); r.arm.get_state.return_value = (0, 0)
        r.arm.get_position.side_effect = [(0, [300, 0, 200, 90, 0, 0]), (0, [310, 0, 200, 90, 0, 0])]
        r.arm.is_tcp_limit.return_value = (0, False); r.arm.get_inverse_kinematics.return_value = (0, [0] * 6)
        r.arm.set_tool_position.return_value = 0
        r.move_tool([10, 0, 0], [[0, 700], [-500, 500], [0, 600]], MOTION)
        kw = r.arm.set_tool_position.call_args.kwargs
        self.assertEqual((kw['x'], kw['y'], kw['z']), (10.0, 0.0, 0.0))
        self.assertEqual((kw['roll'], kw['pitch'], kw['yaw']), (0, 0, 0))

    def test_failures_stop_only_motion_sessions(self):
        r = Robot({}); r.arm = Mock(); r.arm.set_state.return_value = 0
        r.__exit__(ValueError); r.arm.set_state.assert_not_called(); r.motion_session = True
        r.__exit__(ValueError); r.arm.set_state.assert_called_once_with(4)
        with self.assertRaises(RuntimeError): checked((1, []), 'test')

    def test_enable_recovers_clean_stop_and_rechecks_state(self):
        r = Robot({'tcp_offset': [0, 0, 0, 0, 0, 0]}); r.arm = Mock()
        r.arm.get_err_warn_code.side_effect = [(0, [0, 0]), (0, [0, 0])]
        r.arm.get_state.side_effect = [(0, 4), (0, 0)]
        r.arm.get_position.return_value = (0, [300, 0, 200, 0, 0, 0])
        r.arm.tcp_offset = [0, 0, 0, 0, 0, 0]
        r.arm.motion_enable.return_value = 0
        r.arm.set_mode.return_value = 0
        r.arm.set_state.return_value = 0

        r.enable()

        r.arm.motion_enable.assert_called_once_with(True)
        r.arm.set_mode.assert_called_once_with(0)
        r.arm.set_state.assert_called_once_with(0)
        self.assertTrue(r.motion_session)

    def test_enable_does_not_auto_recover_errors_or_pause(self):
        for errors, state in (([1, 0], 4), ([0, 0], 3), ([0, 0], 5)):
            with self.subTest(errors=errors, state=state):
                r = Robot({'tcp_offset': [0, 0, 0, 0, 0, 0]}); r.arm = Mock()
                r.arm.get_err_warn_code.return_value = (0, errors)
                r.arm.get_state.return_value = (0, state)
                with self.assertRaisesRegex(RuntimeError, 'operator attention'):
                    r.enable()
                r.arm.motion_enable.assert_not_called()
                r.arm.set_state.assert_not_called()

    def test_empty_grasp_rejected(self):
        r = Robot({}); r.arm = Mock(); r.arm.robotiq_get_status.return_value = (0, [])
        r.arm.robotiq_status = {'gFLT': 0, 'gOBJ': 3, 'gGTO': 1}
        with self.assertRaises(RuntimeError): r.verify_grasp()
        r.arm.robotiq_status['gOBJ'] = 2; r.verify_grasp()


if __name__ == '__main__': unittest.main()
