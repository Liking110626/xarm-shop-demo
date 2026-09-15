import unittest

import numpy as np

from xarm_grasp.camera_preview import colorize_depth, make_preview


class CameraPreviewTests(unittest.TestCase):
    def test_colorize_depth_marks_invalid_pixels_black(self):
        depth = np.array([[0.0, 200.0, 1100.0, 2000.0, np.nan]], dtype=np.float32)
        result = colorize_depth(depth, 200.0, 2000.0)
        self.assertEqual(result.shape, (1, 5, 3))
        np.testing.assert_array_equal(result[0, 0], [0, 0, 0])
        np.testing.assert_array_equal(result[0, 4], [0, 0, 0])
        self.assertTrue(np.any(result[0, 2] != 0))

    def test_color_preview_keeps_camera_resolution(self):
        frame = {
            "bgr": np.zeros((48, 64, 3), dtype=np.uint8),
            "depth": np.ones((48, 64), dtype=np.float32) * 500.0,
        }
        self.assertEqual(make_preview(frame, False, 200.0, 2000.0, 30.0).shape, (48, 64, 3))
        self.assertEqual(make_preview(frame, True, 200.0, 2000.0, 30.0).shape, (48, 128, 3))

    def test_rejects_inverted_depth_range(self):
        with self.assertRaisesRegex(ValueError, "greater"):
            colorize_depth(np.ones((2, 2)), 1000.0, 500.0)


if __name__ == "__main__":
    unittest.main()
