from pathlib import Path
import math
import sys
import numpy as np
from .coordinates import vector, pose_matrix, check_workspace


def checked(result, operation):
    code = result[0] if isinstance(result, (tuple, list)) else result
    if code != 0:
        raise RuntimeError(f"{operation}: SDK returned {result}")
    return result[1] if isinstance(result, (tuple, list)) else None


class Robot:
    def __init__(self, config):
        self.config = config
        self.arm = None
        self.motion_session = False

    def __enter__(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "xArm-Python-SDK"))
        from xarm.wrapper import XArmAPI
        self.arm = XArmAPI(self.config["ip"], is_radian=False)
        try:
            checked(self.arm.get_state(), "get_state")
            if self.arm.axis != 6:
                raise RuntimeError(f"Expected 6 axes, found {self.arm.axis}")
        except BaseException:
            self.arm.disconnect()
            raise
        return self

    def pose(self):
        return checked(self.arm.get_position(is_radian=False), "get_position")

    def offset(self):
        checked(self.arm.get_position(is_radian=False), "refresh connection")
        return vector(self.arm.tcp_offset, 6, "controller tcp_offset").tolist()

    def ready(self, allow_stopped=False):
        errors = checked(self.arm.get_err_warn_code(), "get_err_warn_code")
        state = checked(self.arm.get_state(), "get_state")
        blocked_states = (3,) if allow_stopped else (3, 4, 5)
        if any(errors) or state in blocked_states:
            raise RuntimeError(f"Robot requires operator attention: errors={errors}, state={state}")
        return state

    def enable(self):
        # A failed motion session deliberately leaves the controller in state 4.
        # A later run may recover that software stop, but must never auto-clear a
        # controller error/warning or bypass pause/other safety states.
        state = self.ready(allow_stopped=True)
        expected = vector(self.config["tcp_offset"], 6, "tcp_offset")
        if not np.allclose(self.offset(), expected, atol=0.05):
            raise ValueError("Controller TCP offset differs from config; set it in xArm Studio first")
        if state == 4:
            print("Robot is stopped (state=4); attempting automatic recovery")
        self.motion_session = True
        checked(self.arm.motion_enable(True), "motion_enable")
        checked(self.arm.set_mode(0), "set_mode")
        checked(self.arm.set_state(0), "set_state")
        self.ready()

    def preflight(self, pose, bounds):
        check_workspace(pose, bounds)
        if checked(self.arm.is_tcp_limit(pose, is_radian=False), "is_tcp_limit"):
            raise ValueError("TCP limit")
        checked(self.arm.get_inverse_kinematics(pose, input_is_radian=False, return_is_radian=False), "IK")

    def move(self, pose, bounds):
        self.ready()
        self.preflight(pose, bounds)
        checked(self.arm.set_position(*pose, speed=self.config["tcp_speed_mm_s"],
                mvacc=self.config["tcp_acc_mm_s2"], is_radian=False,
                wait=True, timeout=30), "move TCP")

    def move_tool(self, delta_tool_mm, bounds, motion):
        """Translate in the current tool frame with exactly zero rotation command."""
        delta = vector(delta_tool_mm, 3, "tool delta")
        if np.allclose(delta, 0):
            return
        self.ready()
        before = vector(self.pose(), 6, "TCP pose")
        before_matrix = pose_matrix(before)
        expected = before.copy()
        expected[:3] += before_matrix[:3, :3] @ delta
        self.preflight(expected.tolist(), bounds)
        checked(self.arm.set_tool_position(
            x=float(delta[0]), y=float(delta[1]), z=float(delta[2]),
            roll=0, pitch=0, yaw=0,
            speed=self.config["tcp_speed_mm_s"], mvacc=self.config["tcp_acc_mm_s2"],
            is_radian=False, wait=True, timeout=30), "move in tool coordinates")
        after = vector(self.pose(), 6, "TCP pose")
        position_error = np.linalg.norm(after[:3] - expected[:3])
        rotation = pose_matrix(before)[:3, :3].T @ pose_matrix(after)[:3, :3]
        angle = math.degrees(math.acos(float(np.clip((np.trace(rotation)-1)/2, -1, 1))))
        if position_error > motion["position_tolerance_mm"]:
            raise RuntimeError(f"Tool move position error {position_error:.3f} mm")
        if angle > motion["orientation_tolerance_deg"]:
            raise RuntimeError(f"TCP orientation changed by {angle:.3f} deg")

    def observe(self, observation, bounds):
        values = vector(observation["values"], 6, "observation pose").tolist()
        self.ready()
        if observation["type"] == "tcp":
            self.move(values, bounds)
        elif observation["type"] == "joint":
            if checked(self.arm.is_joint_limit(values, is_radian=False), "joint limit"):
                raise ValueError("Observation joints outside limits")
            fk = checked(self.arm.get_forward_kinematics(values, input_is_radian=False, return_is_radian=False), "FK")
            check_workspace(fk, bounds)
            checked(self.arm.set_servo_angle(angle=values, speed=self.config["joint_speed_deg_s"],
                    mvacc=self.config["joint_acc_deg_s2"], is_radian=False, wait=True, timeout=30), "observe")
        else:
            raise ValueError("Observation type must be joint or tcp")

    def activate_gripper(self):
        checked(self.arm.robotiq_reset(), "Robotiq reset")
        checked(self.arm.robotiq_set_activate(wait=True, timeout=10), "Robotiq activate")

    def gripper(self, position):
        self.ready()
        checked(self.arm.robotiq_set_position(position, speed=64, force=64, wait=True, timeout=10), "Robotiq move")

    def verify_grasp(self):
        checked(self.arm.robotiq_get_status(), "Robotiq status")
        s = self.arm.robotiq_status
        if s["gFLT"] != 0 or s["gOBJ"] != 2 or s["gGTO"] != 1:
            raise RuntimeError(f"No confirmed object contact while closing: {s}")

    def __exit__(self, exc_type, *args):
        try:
            if exc_type is not None and self.motion_session:
                checked(self.arm.set_state(4), "stop after failure")
        finally:
            self.arm.disconnect()
