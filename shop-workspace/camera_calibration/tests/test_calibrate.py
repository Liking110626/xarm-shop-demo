import unittest
import numpy as np
from camera_calibration.calibrate import solve, validate_held_out
from xarm_grasp.coordinates import pose_matrix


class HandEyeTests(unittest.TestCase):
    def test_synthetic_handeye_recovers_transform(self):
        rng = np.random.default_rng(42); x = pose_matrix([30, -45, 70, 10, 20, -15])
        base_board = pose_matrix([500, 30, 100, 0, 0, 0]); samples = []
        for _ in range(18):
            a = pose_matrix(np.r_[rng.uniform(100, 400, 3), rng.uniform(-60, 60, 3)])
            b = np.linalg.inv(x) @ np.linalg.inv(a) @ base_board
            samples.append({'camera_serial': 'test', 'T_base_flange': a.tolist(), 'T_camera_board': b.tolist()})
        result = solve(samples); np.testing.assert_allclose(result['T_flange_camera'], x, atol=1e-6)
        self.assertFalse(result['validated'])
        checked_result = validate_held_out(samples[:6], result)
        self.assertTrue(checked_result['held_out_consistency_passed'])
        self.assertFalse(checked_result['validated'])
        with self.assertRaises(ValueError): solve([samples[0]] * 12)


if __name__ == "__main__":
    unittest.main()
