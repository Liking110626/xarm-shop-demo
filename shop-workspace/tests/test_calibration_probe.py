import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from xarm_grasp import calibration_probe as probe
from xarm_grasp.__main__ import load, write
from xarm_grasp.geometry import pose_matrix


def robot_record(pose=None):
    return {"T_base_flange": np.eye(4).tolist() if pose is None else pose.tolist(),
            "joint_angles_deg": [0.] * 6, "state": 2, "mode": 0,
            "errors_warnings": [0, 0], "tcp_offset": [0.] * 6, "world_offset": [0.] * 6}


def camera_record():
    return {"serial": "mock-camera", "intrinsics": [460., 461., 320., 240.],
            "distortion": [0.] * 8, "color_profile": {"width": 640, "height": 480},
            "aligned_size": [640, 480]}


class ProbeTests(unittest.TestCase):
    def test_ippe_retains_both_solutions_and_recovers_synthetic_board(self):
        points = probe.object_points(9, 6, 24.)
        k = np.array([[460., 0, 320], [0, 461, 240], [0, 0, 1]])
        expected = pose_matrix([-90, -60, 500, 25, -15, 5])
        rv = cv2.Rodrigues(expected[:3, :3])[0]
        corners = cv2.projectPoints(points, rv, expected[:3, 3], k, np.zeros(8))[0]
        result = probe.estimate_board(points, corners, k, np.zeros(8))
        self.assertEqual(len(result["ippe_candidates"]), 2)
        np.testing.assert_allclose(result["best_ippe"]["T_camera_board"], expected, atol=1e-6)
        self.assertFalse(result["ambiguity"]["flag"])
        self.assertIsNotNone(result["iterative"])

    def test_mean_rotation_spread_is_order_independent_at_angle_wrap(self):
        poses = [pose_matrix([0, 0, 0, 0, 0, n]) for n in (179.9, -179.9, 180.)]
        a, b = probe.pose_spread(poses), probe.pose_spread(poses[::-1])
        self.assertAlmostEqual(a["max_rotation_from_mean_deg"], .1, places=5)
        self.assertAlmostEqual(a["max_rotation_from_mean_deg"], b["max_rotation_from_mean_deg"])

    def test_weak_perspective_two_similar_solutions_are_flagged(self):
        points = probe.object_points(9, 6, 24.)
        k = np.array([[460., 0, 320], [0, 461, 240], [0, 0, 1]])
        pose = pose_matrix([-90, -60, 5000, 15, 0, 0])
        corners = cv2.projectPoints(points, cv2.Rodrigues(pose[:3, :3])[0],
                                    pose[:3, 3], k, np.zeros(8))[0]
        result = probe.estimate_board(points, corners, k, np.zeros(8))
        self.assertTrue(result["ambiguity"]["flag"])
        self.assertGreater(result["ambiguity"]["separation"]["rotation_deg"], 1.)

    def test_index_reversal_is_normalized_without_changing_reference(self):
        corners = np.arange(108, dtype=np.float32).reshape(-1, 1, 2)
        actual, reversed_order = probe.normalize_corner_order(corners[::-1], corners)
        self.assertTrue(reversed_order)
        np.testing.assert_array_equal(actual, corners)

    def test_no_detections_is_review_and_robot_drift_is_detected(self):
        record = {"robot_before": robot_record(),
                  "robot_after": robot_record(pose_matrix([.25, 0, 0, 0, 0, 0]))}
        summary = probe.summarize([record])
        self.assertEqual(summary["status"], "REVIEW")
        self.assertIn("robot_moved_during_burst", summary["review_reasons"])
        self.assertIn("no_valid_board_poses", summary["review_reasons"])

    def test_raw_pixels_saved_before_annotation_and_partial_failure_preserved(self):
        raw = np.full((480, 640, 3), 90, np.uint8)
        corners = np.mgrid[0:9, 0:6].T.reshape(-1, 1, 2).astype(np.float32) * 20 + 80
        vision = {"best_ippe": {"T_camera_board": np.eye(4).tolist(), "reprojection_rms_px": .1},
                  "iterative": {"T_camera_board": np.eye(4).tolist(), "reprojection_rms_px": .1},
                  "ambiguity": {"flag": False}, "ippe_candidates": []}

        class Camera:
            def __init__(self, config): self.calls = 0
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def capture(self):
                self.calls += 1
                if self.calls == 3:
                    raise TimeoutError("test camera failure")
                return dict(camera_record(), bgr=raw.copy(), depth=np.zeros((480, 640)))

        class Robot:
            def __init__(self, config): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass

        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            write(config, {"camera": {}, "robot": {"ip": "test"}})
            args = argparse.Namespace(config=str(config), output=folder, label="A1", frames=5,
                                      cols=9, rows=6, square_mm=24., interval=0.)
            with patch("xarm_grasp.camera.GeminiCamera", Camera), patch("xarm_grasp.robot.Robot", Robot), \
                    patch.object(probe, "snapshot", return_value=robot_record()), \
                    patch.object(cv2, "findChessboardCornersSB", return_value=(True, corners)), \
                    patch.object(probe, "estimate_board", return_value=vision), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(TimeoutError):
                    probe.capture(args)
            report_file = next(Path(folder).glob("*/report.json"))
            report = load(report_file)
            self.assertEqual(report["summary"]["status"], "INCOMPLETE")
            self.assertEqual(report["summary"]["frames_detected"], 2)
            saved = cv2.imread(str(report_file.parent / "frame_001_raw.png"))
            annotated = cv2.imread(str(report_file.parent / "frame_001_annotated.png"))
            np.testing.assert_array_equal(saved, raw)
            self.assertFalse(np.array_equal(saved, annotated))
            self.assertEqual(len(report["frames"][0]["corners_px"]), 54)

    def test_pair_rotation_invariant_and_metadata_mismatch(self):
        x = pose_matrix([20, -30, 40, 5, 10, 20])
        board = pose_matrix([500, 20, 0, 0, 0, 0])
        poses = [pose_matrix([250, 0, 300, 15, 5, 25]), pose_matrix([200, 20, 400, -15, 20, 0])]
        with tempfile.TemporaryDirectory() as folder:
            for i, a in enumerate(poses):
                b = np.linalg.inv(x) @ np.linalg.inv(a) @ board
                r = {"label": str(i), "board": [9, 6, 24.], "robot_ip": "test",
                     "frames": [{"camera": camera_record(), "robot_before": robot_record(a), "vision": {}}],
                     "summary": {"status": "STABLE_BURST", "robot": {"mean_T_base_flange": a.tolist(),
                         "mean_joint_angles_deg": [0] * 6}, "best_ippe": {"mean_pose": b.tolist()}}}
                write(Path(folder) / str(i) / "report.json", r)
            result = probe.compare(folder)
            self.assertLess(result["pairs"][0]["relative_rotation_angle_mismatch_deg"], 1e-5)
            file = Path(folder) / "1" / "report.json"
            r = load(file)
            r["board"][2] = 25.
            write(file, r)
            self.assertEqual(probe.compare(folder)["pairs"][0]["status"], "INCOMPATIBLE_METADATA")


if __name__ == "__main__":
    unittest.main()
