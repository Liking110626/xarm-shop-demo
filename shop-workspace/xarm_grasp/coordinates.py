"""All translations/depths are mm; xArm Euler angles are degrees, Rz Ry Rx."""
import math
import numpy as np


def vector(value, size, name):
    a = np.asarray(value, dtype=float)
    if a.shape != (size,) or not np.isfinite(a).all():
        raise ValueError(f"{name} must contain {size} finite numbers")
    return a


def transform(value):
    t = np.asarray(value, dtype=float)
    if t.shape != (4, 4) or not np.isfinite(t).all():
        raise ValueError("Calibration must be a finite 4x4 matrix")
    r = t[:3, :3]
    if not (np.allclose(t[3], [0, 0, 0, 1]) and
            np.allclose(r.T @ r, np.eye(3), atol=1e-5) and
            np.isclose(np.linalg.det(r), 1, atol=1e-5)):
        raise ValueError("Invalid rigid transform")
    return t


def pose_matrix(pose):
    x, y, z, roll, pitch, yaw = vector(pose, 6, "TCP pose")
    r, p, w = np.deg2rad([roll, pitch, yaw])
    cr, sr, cp, sp, cw, sw = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(w), math.sin(w)
    t = np.eye(4)
    t[:3, :3] = [[cw*cp, cw*sp*sr-sw*cr, cw*sp*cr+sw*sr],
                 [sw*cp, sw*sp*sr+cw*cr, sw*sp*cr-cw*sr],
                 [-sp, cp*sr, cp*cr]]
    t[:3, 3] = [x, y, z]
    return t


def depth_at(depth, uv, radius=3, min_mm=100, max_mm=2000, max_spread_mm=20):
    u, v = (int(round(x)) for x in vector(uv, 2, "pixel"))
    h, w = depth.shape
    if not (radius <= u < w-radius and radius <= v < h-radius):
        raise ValueError("Target pixel too close to image border")
    patch = depth[v-radius:v+radius+1, u-radius:u+radius+1]
    values = patch[np.isfinite(patch) & (patch >= min_mm) & (patch <= max_mm)]
    if values.size < max(5, math.ceil(patch.size * 0.6)):
        raise ValueError("Insufficient valid depth near target")
    if np.percentile(values, 90) - np.percentile(values, 10) > max_spread_mm:
        raise ValueError("Unstable depth / object edge near target")
    return float(np.median(values))


def deproject(uv, depth_mm, intrinsics, distortion=None):
    fx, fy, cx, cy = vector(intrinsics, 4, "intrinsics")
    if fx <= 0 or fy <= 0 or not np.isfinite(depth_mm) or depth_mm <= 0:
        raise ValueError("Invalid focal length or depth")
    u, v = vector(uv, 2, "pixel")
    if distortion is not None and np.any(distortion):
        import cv2
        k = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.]])
        ray = cv2.undistortPoints(np.array([[[u, v]]]), k, np.asarray(distortion, float))[0, 0]
    else:
        ray = np.array([(u-cx)/fx, (v-cy)/fy])
    return np.array([ray[0]*depth_mm, ray[1]*depth_mm, depth_mm])


def pixel_to_camera(uv, depth, intrinsics, distortion=None, **depth_settings):
    """Return [x,y,z] mm from an aligned depth image and a color-image pixel."""
    z = depth_at(depth, uv, **depth_settings)
    return deproject(uv, z, intrinsics, distortion)


def camera_to_tcp(camera_point_mm, tcp_offset, flange_from_camera):
    """Return a point in the configured TCP frame, without a robot/base pose.

    tcp_offset is [x,y,z,roll,pitch,yaw] of TCP relative to flange (mm/degrees).
    flange_from_camera is T_flange_camera from eye-in-hand calibration.
    The output is a visible surface point, before the grasp depth offset.
    """
    tcp_from_camera = np.linalg.inv(pose_matrix(tcp_offset)) @ transform(flange_from_camera)
    return (tcp_from_camera @ np.r_[vector(camera_point_mm, 3, "camera point"), 1])[:3]


def tcp_to_base(tcp_point_mm, tcp_pose):
    """Return a base-frame point using the TCP pose recorded at image capture."""
    return (pose_matrix(tcp_pose) @ np.r_[vector(tcp_point_mm, 3, "TCP point"), 1])[:3]


def check_workspace(pose, bounds):
    xyz = vector(pose, 6, "pose")[:3]
    b = np.asarray(bounds, float)
    if b.shape != (3, 2) or not np.isfinite(b).all() or np.any(b[:, 0] >= b[:, 1]):
        raise ValueError("workspace_mm must be [[xmin,xmax],[ymin,ymax],[zmin,zmax]]")
    if np.any(xyz < b[:, 0]) or np.any(xyz > b[:, 1]):
        raise ValueError(f"TCP outside workspace: {xyz.tolist()}")


def main():
    import argparse
    import json
    from .config import DEFAULT_CONFIG, load
    parser = argparse.ArgumentParser(description="Convert camera XYZ mm to TCP XYZ mm; no hardware access")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--camera-point", nargs=3, type=float, required=True, metavar=("X", "Y", "Z"))
    args = parser.parse_args()
    try:
        config = load(args.config)
        point = camera_to_tcp(args.camera_point, config["robot"]["tcp_offset"],
                              config["calibration"]["T_flange_camera"])
        print(json.dumps({"camera_point_mm": args.camera_point, "tcp_point_mm": point.tolist(),
                          "calibration_validated": config["calibration"].get("validated") is True}, indent=2))
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f"Stopped: {exc}\n")


if __name__ == "__main__":
    main()
