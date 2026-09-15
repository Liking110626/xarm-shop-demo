"""Offline API and orchestration regression tests; all devices/network are mocked."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from xarm_grasp.coordinates import camera_to_tcp, pose_matrix, tcp_to_base
from xarm_grasp.motion import make_tool_axis_plan_from_tcp, grasp_from_observation
from xarm_grasp.config import validate_common_execution
from vision.ocr import recognize_receipt, recognize_receipt_file
from xarm_grasp.__main__ import run, run_isolated_step
from vision.yolo import (get_product_position, load_saved_frame, locate_product,
                             recognize_product, save_detection, acquire_product)


def fixture():
    product = {"class_id": 1, "aliases": ["sprite"], "model": "products.pt",
               "grasp_depth_offset_mm": 27, "open_position": 0, "close_position": 85}
    config = {
        "motion_enabled": True,
        "confidence": .6, "depth": {},
        "camera": {"serial": "test-camera"},
        "robot": {"tcp_offset": [0, 0, 174, 0, 0, 0], "tcp_speed_mm_s": 20,
                  "tcp_acc_mm_s2": 100, "joint_speed_deg_s": 5, "joint_acc_deg_s2": 20},
        "calibration": {"camera_serial": "test-camera", "validated": True,
                        "T_flange_camera": pose_matrix([30, 20, -226, 0, 0, 0]).tolist()},
        "products": {"Sprite": product},
        "initial_pose": {"type": "joint", "values": [0] * 6},
        "grasp_observation": {"type": "joint", "values": [2] * 6},
        "receipt": {"observation": {"type": "joint", "values": [1] * 6}},
        "workspace_mm": [[0, 700], [-500, 500], [0, 600]],
        "tool_motion": {
            "lateral_axis": "y", "vertical_axis": "x", "depth_axis": "z",
            "approach_axis_sign": 1, "lift_axis_sign": -1,
            "lift_mm": 20, "axis_deadband_mm": 1, "max_alignment_mm": 300,
            "min_approach_mm": 20, "max_approach_mm": 600,
            "vertical_axis_min_up_component": .7,
            "position_tolerance_mm": 2, "orientation_tolerance_deg": .5,
        },
    }
    frame = {"bgr": np.zeros((64, 64, 3), np.uint8),
             "depth": np.full((64, 64), 500., np.float32),
             "intrinsics": [500, 500, 32, 32], "distortion": None, "serial": "test-camera"}
    return config, product, frame


def box(class_id=1):
    value = Mock()
    value.cls.item.return_value = class_id
    value.conf.item.return_value = .9
    value.xyxy = [Mock()]
    value.xyxy[0].cpu.return_value.numpy.return_value = np.array([20, 20, 44, 44])
    return value


def model_with(*boxes):
    model = Mock()
    model.predict.return_value = [SimpleNamespace(boxes=list(boxes))]
    return model


class LocalizationTests(unittest.TestCase):
    def test_failed_recognition_preserves_frame_and_replaces_stale_plan(self):
        cases = [('no_boxes', [], False, RuntimeError),
                 ('other_class', [box(2)], False, RuntimeError),
                 ('multiple_targets', [box(), box()], False, RuntimeError),
                 ('invalid_depth', [box()], True, ValueError)]
        for label, boxes, bad_depth, error_type in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                config, product, frame = fixture()
                frame['bgr'][:] = [20, 40, 60]
                if bad_depth:
                    frame['depth'][:] = 0
                output = Path(tmp)
                (output / 'result.json').write_text(json.dumps({
                    'status': 'step_4_grasp_planned', 'tool_axis_plan': {'old': True}}))
                model = model_with(*boxes)
                model.names = {1: 'Sprite', 2: 'Fanta'}
                prediction = model.predict.return_value

                def infer(*args, **kwargs):
                    # Raw evidence and invalidation must precede the model call.
                    restored = load_saved_frame(tmp)
                    np.testing.assert_array_equal(restored['bgr'], frame['bgr'])
                    self.assertEqual(json.loads((output / 'result.json').read_text())['status'],
                                     'recognition_pending')
                    return prediction

                model.predict.side_effect = infer
                camera = Mock()
                camera.capture.return_value = frame
                with redirect_stdout(io.StringIO()), self.assertRaises(error_type):
                    acquire_product(camera, model, product, 'Sprite', config, output)
                restored = load_saved_frame(tmp)
                np.testing.assert_array_equal(restored['bgr'], frame['bgr'])
                np.testing.assert_array_equal(restored['depth'], frame['depth'])
                result = json.loads((output / 'result.json').read_text())
                self.assertEqual(result['status'], 'recognition_failed')
                self.assertNotIn('tool_axis_plan', result)
                self.assertEqual(result['class_id'], 1)
                self.assertEqual(result['confidence_threshold'], .6)
                target_boxes = [b for b in boxes if int(b.cls.item()) == product['class_id']]
                self.assertEqual(len(result['detections']), len(target_boxes))
                self.assertEqual(result['inference_classes'], [product['class_id']])
                self.assertIn('error', result)
                self.assertTrue((output / 'detection.jpg').is_file())
                model.predict.assert_called_once()
                self.assertEqual(model.predict.call_args.kwargs['classes'], [product['class_id']])

    def test_inference_exception_keeps_raw_frame(self):
        config, product, frame = fixture()
        camera = Mock()
        camera.capture.return_value = frame
        model = Mock()
        model.predict.side_effect = RuntimeError('inference failed')
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'inference failed'):
                acquire_product(camera, model, product, 'Sprite', config, tmp)
            np.testing.assert_array_equal(load_saved_frame(tmp)['bgr'], frame['bgr'])
            result = json.loads((Path(tmp) / 'result.json').read_text())
            self.assertEqual(result['status'], 'recognition_failed')
            self.assertNotIn('detections', result)

    def test_camera_to_tcp_inverts_full_offset_including_rotation(self):
        # TCP origin in flange is [10,20,30], TCP orientation is +90 about Z.
        actual = camera_to_tcp([10, 120, 30], [10, 20, 30, 0, 0, 90], np.eye(4))
        np.testing.assert_allclose(actual, [100, 0, 0], atol=1e-8)
        np.testing.assert_allclose(tcp_to_base(actual, [300, 0, 200, 0, 0, 90]),
                                   [300, 100, 200], atol=1e-8)

    def test_frame_function_returns_both_frames_without_applying_grasp_offset(self):
        config, product, frame = fixture()
        result = recognize_product(frame, model_with(box(2), box()), product, config, "Sprite")
        np.testing.assert_allclose(result["camera_point_mm"], [0, 0, 500])
        np.testing.assert_allclose(result["tcp_point_mm"], [30, 20, 100])
        plan = make_tool_axis_plan_from_tcp(result["tcp_point_mm"], [300, 0, 200, 0, 90, 0],
                                            27, config["tool_motion"], config["workspace_mm"])
        np.testing.assert_allclose(plan["target_delta_tool_mm"], [30, 20, 127])
        self.assertEqual([s["delta_tool_mm"] for s in plan["steps"]],
                         [[0, 20, 0], [30, 0, 0], [0, 0, 127], [-20, 0, 0], [0, 0, -127]])

    def test_requested_class_is_passed_to_model_and_other_classes_are_ignored(self):
        config, product, frame = fixture()
        product['class_id'] = 2
        model = model_with(box(0), box(1), box(2), box(5))
        diagnostics = {}
        report = recognize_product(frame, model, product, config, 'Fanta',
                                   include_tcp=False, diagnostics=diagnostics)
        self.assertEqual(model.predict.call_args.kwargs['classes'], [2])
        self.assertEqual(report['class_id'], 2)
        self.assertEqual([d['class_id'] for d in diagnostics['detections']], [2])
        self.assertEqual(diagnostics['target_count'], 1)

    def test_camera_only_needs_no_calibration_or_robot_config(self):
        config, product, frame = fixture()
        del config["calibration"], config["robot"]
        report = recognize_product(frame, model_with(box()), product, config, include_tcp=False)
        self.assertNotIn("tcp_point_mm", report)
        self.assertEqual(report["camera_point_mm"], [0, 0, 500])

    def test_bad_depth_wrong_serial_and_ambiguous_detections_raise(self):
        config, product, frame = fixture()
        for boxes in ([], [box(), box()]):
            with self.assertRaisesRegex(RuntimeError, "exactly one target"):
                recognize_product(frame, model_with(*boxes), product, config)
        frame["serial"] = "different-camera"
        with self.assertRaisesRegex(ValueError, "differs"):
            recognize_product(frame, model_with(box()), product, config)
        frame["serial"] = "test-camera"
        frame["depth"][:] = 0
        with self.assertRaisesRegex(ValueError, "Insufficient valid depth"):
            recognize_product(frame, model_with(box()), product, config)

    def test_capture_helper_and_xyz_helper_close_camera_without_robot(self):
        config, product, frame = fixture()
        with patch("vision.yolo.load", return_value=config), \
             patch("vision.yolo.load_model", return_value=model_with(box())), \
             patch("vision.camera.GeminiCamera") as camera_cls, \
             patch("xarm_grasp.robot.Robot") as robot_cls:
            camera = camera_cls.return_value.__enter__.return_value
            camera.capture.return_value = frame
            point = get_product_position("sprite")
            np.testing.assert_allclose(point, [30, 20, 100])
            camera_cls.return_value.__exit__.assert_called_once()
            robot_cls.assert_not_called()
            with self.assertRaises(ValueError):
                get_product_position("sprite", coordinate_system="base")

    def test_saved_frame_replays_without_camera_and_preserves_raw_pixels(self):
        config, product, frame = fixture()
        model = model_with(box())
        report = recognize_product(frame, model, product, config)
        with tempfile.TemporaryDirectory() as tmp:
            save_detection(frame, report, tmp)
            restored = load_saved_frame(tmp)
            np.testing.assert_array_equal(restored["bgr"], frame["bgr"])
            np.testing.assert_array_equal(restored["depth"], frame["depth"])
            with patch("vision.yolo.load_model", return_value=model), \
                 patch("vision.camera.GeminiCamera") as camera_cls:
                path = Path(tmp) / "config.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                replay = locate_product("sprite", path, frame_dir=tmp)
                self.assertEqual(replay["tcp_point_mm"], report["tcp_point_mm"])
                camera_cls.assert_not_called()


class ObservationFallbackTests(unittest.TestCase):
    def setup_scene(self):
        config, product, frame = fixture()
        config['grasp_observation_fallback'] = {'type': 'joint', 'values': [3] * 6}
        camera, robot = Mock(), Mock()
        camera.capture.return_value = frame
        robot.offset.return_value = config['robot']['tcp_offset']
        robot.pose.return_value = [300, 0, 200, 0, 90, 0]
        return config, product, frame, camera, robot

    def test_primary_success_does_not_move_to_fallback(self):
        config, product, _, camera, robot = self.setup_scene()
        with tempfile.TemporaryDirectory() as tmp, patch('time.sleep'), redirect_stdout(io.StringIO()):
            report = grasp_from_observation(camera, robot, model_with(box()), product,
                                             'Sprite', config, tmp, plan_only=True)
            self.assertEqual(report['observation_used'], 'primary')
            robot.observe.assert_called_once_with(config['grasp_observation'], config['workspace_mm'])
            robot.activate_gripper.assert_not_called()

    def test_missing_primary_uses_fallback_pose_and_keeps_both_frames(self):
        config, _, frame, camera, robot = self.setup_scene()
        fallback_frame = {**frame, 'bgr': np.full_like(frame['bgr'], 100)}
        camera.capture.side_effect = [frame, frame, fallback_frame]  # isolated step 4 warmup + two views
        model = model_with()
        model.predict.side_effect = [[SimpleNamespace(boxes=[])], [SimpleNamespace(boxes=[box()])]]
        fallback_pose = [350, 10, 210, 0, 90, 0]
        robot.pose.side_effect = [[300, 0, 200, 0, 90, 0]] * 2 + [fallback_pose] * 2
        with tempfile.TemporaryDirectory() as tmp, patch('time.sleep'), \
             patch('vision.camera.GeminiCamera') as camera_cls, \
             patch('xarm_grasp.robot.Robot') as robot_cls, \
             patch('xarm_grasp.__main__.load_model', return_value=model), redirect_stdout(io.StringIO()):
            camera.serial = 'test-camera'
            camera_cls.return_value.__enter__.return_value = camera
            robot_cls.return_value.__enter__.return_value = robot
            run_isolated_step(SimpleNamespace(item='Sprite'), Path(tmp) / 'config.json', config, Path(tmp), 4)
            report = json.loads((Path(tmp) / 'result.json').read_text())
            self.assertEqual(report['status'], 'step_4_grasp_planned')
            self.assertEqual(report['tcp_at_capture'], fallback_pose)
            self.assertEqual(report['observation_used'], 'fallback')
            self.assertEqual([c.args[0] for c in robot.observe.call_args_list],
                             [config['grasp_observation'], config['grasp_observation_fallback']])
            np.testing.assert_allclose(report['base_point_mm'], tcp_to_base(report['tcp_point_mm'], fallback_pose))
            attempts = report['observation_attempts']
            np.testing.assert_array_equal(load_saved_frame(Path(tmp) / attempts[0]['evidence_dir'])['bgr'], frame['bgr'])
            np.testing.assert_array_equal(load_saved_frame(Path(tmp) / attempts[1]['evidence_dir'])['bgr'], fallback_frame['bgr'])
            robot.activate_gripper.assert_not_called()
            robot.pose.side_effect = None
            robot.pose.return_value = fallback_pose
            run_isolated_step(SimpleNamespace(item=None), Path(tmp) / 'config.json', config, Path(tmp), 5)
            self.assertEqual(robot.move_tool.call_count, 5)

    def test_both_missing_stop_without_grasp_and_keep_diagnostics(self):
        config, product, _, camera, robot = self.setup_scene()
        with tempfile.TemporaryDirectory() as tmp, patch('time.sleep'), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'all 2 observation'):
                grasp_from_observation(camera, robot, model_with(), product, 'Sprite', config, tmp)
            report = json.loads((Path(tmp) / 'result.json').read_text())
            self.assertEqual(report['status'], 'recognition_failed')
            self.assertEqual(report['target_count'], 0)
            self.assertEqual(len(report['observation_attempts']), 2)
            self.assertEqual(len(list((Path(tmp) / 'observations').rglob('color.png'))), 2)
            self.assertNotIn('tool_axis_plan', report)
            robot.activate_gripper.assert_not_called()

    def test_ambiguity_depth_failure_and_robot_drift_do_not_trigger_fallback(self):
        for reason in ('ambiguous', 'depth', 'drift', 'inference'):
            with self.subTest(reason=reason):
                config, product, frame, camera, robot = self.setup_scene()
                model = model_with(box(), box()) if reason == 'ambiguous' else model_with(box())
                if reason == 'depth':
                    frame['depth'][:] = 0
                if reason == 'drift':
                    model = model_with()
                    robot.pose.side_effect = [[300, 0, 200, 0, 90, 0], [301, 0, 200, 0, 90, 0]]
                if reason == 'inference':
                    model.predict.side_effect = RuntimeError('inference failed')
                with tempfile.TemporaryDirectory() as tmp, patch('time.sleep'), redirect_stdout(io.StringIO()):
                    with self.assertRaises((ValueError, RuntimeError)):
                        grasp_from_observation(camera, robot, model, product, 'Sprite', config, tmp)
                self.assertEqual(robot.observe.call_count, 1)
                robot.activate_gripper.assert_not_called()

    def test_invalid_fallback_is_rejected_by_common_validation(self):
        config, _, _, _, _ = self.setup_scene()
        config['grasp_observation_fallback']['values'] = None
        with self.assertRaises(ValueError):
            validate_common_execution(config)


class OcrFunctionTests(unittest.TestCase):
    def test_returns_selected_item_without_requiring_output_directory(self):
        config, _, frame = fixture()
        response = Mock()
        response.json.return_value = {"md_results": "sprite"}
        with patch.dict("os.environ", {"ZHIPUAI_API_KEY": "test-only"}), \
             patch("requests.post", return_value=response), \
             patch("cv2.imwrite") as save:
            result = recognize_receipt(frame["bgr"], config)
            self.assertEqual(result["selected_item"], "Sprite")
            save.assert_not_called()

    def test_ocr_file_outputs_evidence_and_rejects_multiple_products(self):
        import cv2
        config, product, frame = fixture()
        config["products"]["Fanta"] = {**product, "aliases": ["fanta"]}
        response = Mock()
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict("os.environ", {"ZHIPUAI_API_KEY": "test-only"}), \
             patch("requests.post", return_value=response):
            path = Path(tmp) / "input.png"
            cv2.imencode(".png", frame["bgr"])[1].tofile(path)
            response.json.return_value = {"md_results": "sprite"}
            result = recognize_receipt_file(path, config, tmp)
            self.assertEqual(result["selected_item"], "Sprite")
            self.assertTrue((Path(tmp) / "receipt" / "receipt_ocr.json").is_file())
            response.json.return_value = {"md_results": "sprite fanta"}
            with self.assertRaisesRegex(ValueError, "multiple configured"):
                recognize_receipt_file(path, config, tmp)
            rejected = json.loads((Path(tmp) / "receipt" / "receipt_result.json").read_text())
            self.assertEqual(rejected["status"], "rejected")


class WorkflowTests(unittest.TestCase):
    def exercise_workflow(self, plan_only=False, preflight_failure=False, step_test=False):
        config, _, frame = fixture()
        events = []
        robot = Mock()
        robot.pose.return_value = [300, 0, 200, 0, 90, 0]
        robot.offset.return_value = config["robot"]["tcp_offset"]
        robot.observe.side_effect = lambda observation, bounds: events.append(("pose", observation["values"][0]))
        robot.activate_gripper.side_effect = lambda: events.append(("activate",))
        robot.gripper.side_effect = lambda position: events.append(("gripper", position))
        robot.move_tool.side_effect = lambda delta, *args: events.append(("move", delta))
        if preflight_failure:
            robot.preflight.side_effect = ValueError("IK failed")

        def ocr(*args):
            events.append(("ocr",))
            return {"selected_item": "Sprite"}

        def confirm(prompt):
            events.append(("confirm", int(prompt.split('[步骤 ')[1].split('/')[0])))
            return ''

        with tempfile.TemporaryDirectory() as tmp, \
             patch("xarm_grasp.__main__.load", return_value=config), \
             patch("xarm_grasp.__main__.load_model", return_value=model_with(box())), \
             patch("xarm_grasp.__main__.validate_receipt_settings"), \
             patch("xarm_grasp.__main__.recognize_receipt_frame", side_effect=ocr), \
             patch("vision.camera.GeminiCamera") as camera_cls, \
             patch("xarm_grasp.robot.Robot") as robot_cls, \
             patch("time.sleep"), patch("builtins.input", side_effect=confirm), \
             redirect_stdout(io.StringIO()):
            camera = camera_cls.return_value.__enter__.return_value
            camera.serial = "test-camera"
            camera.capture.return_value = frame
            robot_cls.return_value.__enter__.return_value = robot
            args = SimpleNamespace(config="config.json", output=tmp, item=None,
                                   receipt_image=None, execute=True, plan_only=plan_only,
                                   step_test=step_test)
            if preflight_failure:
                with self.assertRaisesRegex(ValueError, "IK failed"):
                    run(args)
                robot.activate_gripper.assert_not_called()
                robot.move_tool.assert_not_called()
                return
            run(args)
            result = json.loads((Path(tmp) / "result.json").read_text(encoding="utf-8"))
        if step_test:
            self.assertEqual([event[1] for event in events if event[0] == "confirm"],
                             [1, 2, 3, 4, 5, 6])
            events = [event for event in events if event[0] != "confirm"]
        self.assertEqual(robot.preflight.call_count, 5)
        self.assertEqual(events[:4], [("pose", 0), ("pose", 1), ("ocr",), ("pose", 0)])
        self.assertEqual(events[4], ("pose", 2))
        self.assertEqual(events[-1], ("pose", 0))
        if plan_only:
            robot.activate_gripper.assert_not_called()
            robot.gripper.assert_not_called()
            robot.move_tool.assert_not_called()
            self.assertEqual(result["status"], "planned_no_grasp_returned_to_initial")
        else:
            self.assertEqual(events[5:-1], [
                ("activate",), ("gripper", 0), ("move", [0, 20, 0]),
                ("move", [30, 0, 0]), ("move", [0, 0, 127]),
                ("gripper", 85), ("move", [-20, 0, 0]), ("move", [0, 0, -127]),
            ])
            self.assertEqual(robot.verify_grasp.call_count, 3)
            self.assertEqual(result["status"], "holding_object_at_initial_pose")

    def test_receipt_to_grasp_order_and_return(self):
        self.exercise_workflow()

    def test_plan_only_observes_but_never_moves_gripper_or_grasp_axes(self):
        self.exercise_workflow(plan_only=True)

    def test_preflight_failure_prevents_grasp(self):
        self.exercise_workflow(preflight_failure=True)

    def test_six_step_mode_confirms_every_stage(self):
        self.exercise_workflow(step_test=True)

    def test_six_step_mode_requires_complete_receipt_flow(self):
        base = dict(config="config.json", output="unused", receipt_image=None,
                    execute=True, plan_only=False, step_test=True)
        with self.assertRaisesRegex(ValueError, 'do not pass'):
            run(SimpleNamespace(**base, item="Sprite"))
        with self.assertRaisesRegex(ValueError, 'cannot be used together'):
            run(SimpleNamespace(**{**base, "item": None, "plan_only": True}))

    def test_isolated_initial_step_moves_only_to_initial_pose(self):
        config, _, _ = fixture()
        robot = Mock()
        with tempfile.TemporaryDirectory() as tmp, \
             patch("xarm_grasp.__main__.load", return_value=config), \
             patch("xarm_grasp.__main__.validate_common_execution"), \
             patch("xarm_grasp.robot.Robot") as robot_cls, \
             patch("vision.camera.GeminiCamera") as camera_cls, \
             redirect_stdout(io.StringIO()):
            robot_cls.return_value.__enter__.return_value = robot
            run(SimpleNamespace(config="config.json", output=tmp, item=None,
                                receipt_image=None, execute=True, plan_only=False,
                                step_test=False, step=1))
        robot.enable.assert_called_once_with()
        robot.observe.assert_called_once_with(config["initial_pose"], config["workspace_mm"])
        camera_cls.assert_not_called()

    def test_isolated_grasp_reuses_plan_only_at_matching_capture_pose(self):
        config, _, _ = fixture()
        capture_pose = [300, 0, 200, 0, 90, 0]
        saved = {
            "item": "Sprite", "tcp_point_mm": [30, 20, 100],
            "tcp_at_capture": capture_pose, "status": "step_4_grasp_planned",
        }
        args = SimpleNamespace(item=None)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "result.json").write_text(json.dumps(saved), encoding="utf-8")
            robot = Mock()
            robot.pose.return_value = capture_pose
            with patch("xarm_grasp.__main__.validate_common_execution"), \
                 patch("xarm_grasp.robot.Robot") as robot_cls, \
                 patch("vision.camera.GeminiCamera") as camera_cls, \
                 redirect_stdout(io.StringIO()):
                robot_cls.return_value.__enter__.return_value = robot
                run_isolated_step(args, Path(tmp) / "config.json", config, output, 5)
            camera_cls.assert_not_called()
            self.assertEqual(robot.preflight.call_count, 5)
            self.assertEqual(robot.move_tool.call_count, 5)
            robot.gripper.assert_any_call(0)
            robot.gripper.assert_any_call(85)
            result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "holding_object_after_pullout")

    def test_isolated_grasp_rejects_robot_moved_since_step_four(self):
        config, _, _ = fixture()
        saved = {
            "item": "Sprite", "tcp_point_mm": [30, 20, 100],
            "tcp_at_capture": [300, 0, 200, 0, 90, 0],
            "status": "step_4_grasp_planned",
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "result.json").write_text(json.dumps(saved), encoding="utf-8")
            robot = Mock()
            robot.pose.return_value = [310, 0, 200, 0, 90, 0]
            with patch("xarm_grasp.__main__.validate_common_execution"), \
                 patch("xarm_grasp.robot.Robot") as robot_cls:
                robot_cls.return_value.__enter__.return_value = robot
                with self.assertRaisesRegex(RuntimeError, 'rerun step 4'):
                    run_isolated_step(SimpleNamespace(item=None), Path(tmp) / "config.json",
                                      config, output, 5)
            robot.activate_gripper.assert_not_called()
            robot.move_tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
