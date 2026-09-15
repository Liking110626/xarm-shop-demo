"""Read-only, multi-frame chessboard diagnostics; never commands robot motion.

One burst is one stationary pose, not N independent hand-eye samples.
Pixel/error thresholds are diagnostic heuristics, not calibration acceptance.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

import cv2
import numpy as np

from xarm_grasp.config import DEFAULT_CONFIG, load, write
from . import DATA_DIR
from xarm_grasp.coordinates import pose_matrix


LIMITS = {"robot_translation_mm": 0.1, "robot_rotation_deg": 0.1,
          "joint_rotation_deg": 0.1, "vision_translation_mm": 0.5,
          "vision_rotation_deg": 0.2, "reprojection_px": 1.0,
          "ambiguity_gap_px": 0.1, "ambiguity_ratio": 1.5,
          "ambiguity_separation_deg": 0.5}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def rotation_angle(r):
    return float(np.degrees(np.linalg.norm(cv2.Rodrigues(np.asarray(r, float))[0])))


def mean_pose(poses):
    poses = np.asarray(poses, float)
    u, _, vt = np.linalg.svd(np.mean(poses[:, :3, :3], axis=0))
    fix = np.eye(3)
    fix[2, 2] = np.linalg.det(u @ vt)
    result = np.eye(4)
    result[:3, :3] = u @ fix @ vt
    result[:3, 3] = np.mean(poses[:, :3, 3], axis=0)
    return result


def pose_spread(poses):
    mean = mean_pose(poses)
    translations = [float(np.linalg.norm(np.asarray(p)[:3, 3] - mean[:3, 3])) for p in poses]
    rotations = [rotation_angle(mean[:3, :3].T @ np.asarray(p)[:3, :3]) for p in poses]
    return {"mean_pose": mean.tolist(),
            "max_translation_from_mean_mm": max(translations),
            "rms_translation_from_mean_mm": float(np.sqrt(np.mean(np.square(translations)))),
            "max_rotation_from_mean_deg": max(rotations),
            "rms_rotation_from_mean_deg": float(np.sqrt(np.mean(np.square(rotations))))}


def pose_delta(first, second):
    a, b = np.asarray(first), np.asarray(second)
    return {"translation_mm": float(np.linalg.norm(a[:3, 3] - b[:3, 3])),
            "rotation_deg": rotation_angle(a[:3, :3].T @ b[:3, :3])}


def object_points(cols, rows, square_mm):
    points = np.zeros((cols * rows, 3), np.float64)
    points[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square_mm
    return points


def normalize_corner_order(corners, reference):
    # Resolve a possible 180-degree indexing reversal WITHIN a stationary burst.
    # A first frame in a new session remains arbitrary; compare reports warn on this.
    if reference is None:
        return corners, False
    direct = np.mean(np.square(corners - reference))
    reverse = np.mean(np.square(corners[::-1] - reference))
    return (corners[::-1].copy(), True) if reverse < direct else (corners, False)


def candidate(points, corners, k, distortion, rvec, tvec):
    r = cv2.Rodrigues(rvec)[0]
    t = np.asarray(tvec).reshape(3)
    if not np.isfinite(r).all() or not np.isfinite(t).all():
        return None
    if np.min((points @ r.T + t)[:, 2]) <= 0:
        return None
    projected = cv2.projectPoints(points, rvec, tvec, k, distortion)[0]
    error = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - corners.reshape(-1, 2)) ** 2, axis=1))))
    matrix = np.eye(4)
    matrix[:3, :3], matrix[:3, 3] = r, t
    return {"T_camera_board": matrix.tolist(), "reprojection_rms_px": error}


def estimate_board(points, corners, k, distortion):
    output = cv2.solvePnPGeneric(points, corners, k, distortion, flags=cv2.SOLVEPNP_IPPE)
    solutions = []
    if output[0]:
        for rvec, tvec in zip(output[1], output[2]):
            c = candidate(points, corners, k, distortion, rvec, tvec)
            if c is not None:
                solutions.append(c)
    solutions.sort(key=lambda c: c["reprojection_rms_px"])
    if not solutions:
        raise ValueError("No finite, positive-depth IPPE solution")
    ambiguity = {"flag": False, "note": "heuristic; not proof of a wrong pose"}
    if len(solutions) >= 2:
        e1, e2 = [c["reprojection_rms_px"] for c in solutions[:2]]
        separation = pose_delta(solutions[0]["T_camera_board"], solutions[1]["T_camera_board"])
        ratio = e2 / max(e1, 1e-9)
        ambiguity.update(gap_px=e2-e1, error_ratio=ratio, separation=separation,
                         flag=bool(separation["rotation_deg"] >= LIMITS["ambiguity_separation_deg"]
                                   and e2 <= LIMITS["reprojection_px"]
                                   and (e2-e1 < LIMITS["ambiguity_gap_px"] or ratio < LIMITS["ambiguity_ratio"])))
    # Keep the legacy estimator too, to diagnose rather than silently change its output.
    ok, rv, tv = cv2.solvePnP(points, corners, k, distortion, flags=cv2.SOLVEPNP_ITERATIVE)
    iterative = candidate(points, corners, k, distortion, rv, tv) if ok else None
    return {"ippe_candidates": solutions, "best_ippe": solutions[0],
            "iterative": iterative, "ambiguity": ambiguity}


def snapshot(robot):
    from xarm_grasp.robot import checked
    started = time.monotonic_ns()
    tcp = robot.pose()
    offset = robot.offset()
    joints = checked(robot.arm.get_servo_angle(is_radian=False, is_real=True), "get actual joints")[:6]
    state = checked(robot.arm.get_state(), "get_state")
    errors = checked(robot.arm.get_err_warn_code(), "get_err_warn_code")
    return {"host_utc": utc_now(), "host_read_start_monotonic_ns": started,
            "host_read_end_monotonic_ns": time.monotonic_ns(),
            "tcp_pose": tcp, "tcp_offset": offset, "joint_angles_deg": joints,
            "state": state, "mode": robot.arm.mode, "errors_warnings": errors,
            "world_offset": list(robot.arm.world_offset),
            "T_base_flange": (pose_matrix(tcp) @ np.linalg.inv(pose_matrix(offset))).tolist()}


def summarize(records):
    good = [r for r in records if "vision" in r]
    reasons = []
    if len(good) != len(records):
        reasons.append("one_or_more_frames_failed_detection_or_pose_estimation")
    snapshots = [r[key] for r in records for key in ("robot_before", "robot_after") if key in r]
    result = {"frames_attempted": len(records), "frames_detected": len(good), "review_reasons": reasons}
    if snapshots:
        first = snapshots[0]
        delta = [pose_delta(first["T_base_flange"], s["T_base_flange"]) for s in snapshots]
        dt, dr = max(d["translation_mm"] for d in delta), max(d["rotation_deg"] for d in delta)
        joints = np.array([s["joint_angles_deg"] for s in snapshots])
        jd = np.abs((joints - joints[0] + 180) % 360 - 180)
        result["robot"] = {"max_translation_from_first_mm": dt, "max_rotation_from_first_deg": dr,
                           "max_joint_change_deg": float(jd.max()),
                           "mean_joint_angles_deg": (joints[0] + np.mean((joints-joints[0]+180) % 360-180, axis=0)).tolist(),
                           "mean_T_base_flange": mean_pose([s["T_base_flange"] for s in snapshots]).tolist()}
        if dt > LIMITS["robot_translation_mm"] or dr > LIMITS["robot_rotation_deg"] or jd.max() > LIMITS["joint_rotation_deg"]:
            reasons.append("robot_moved_during_burst")
        if any(s["state"] != 2 or s["mode"] != 0 or any(s["errors_warnings"]) for s in snapshots):
            reasons.append("robot_not_in_idle_position_mode_or_has_error_warning")
        if any(not np.allclose(s[k], first[k], atol=1e-6, rtol=0) for s in snapshots for k in ("tcp_offset", "world_offset")):
            reasons.append("robot_coordinate_offsets_changed")
    if good:
        camera_signature = lambda r: json.dumps({k: r["camera"][k] for k in (
            "serial", "intrinsics", "distortion", "color_profile", "aligned_size")}, sort_keys=True)
        if len({camera_signature(r) for r in good}) != 1:
            reasons.append("camera_parameters_changed")
        for key in ("best_ippe", "iterative"):
            poses = [r["vision"][key]["T_camera_board"] for r in good if r["vision"][key] is not None]
            if poses:
                result[key] = pose_spread(poses)
        result["ambiguous_frames"] = sum(r["vision"]["ambiguity"]["flag"] for r in good)
        result["max_reprojection_rms_px"] = max(r["vision"]["best_ippe"]["reprojection_rms_px"] for r in good)
        result["mean_corner_motion_max_px"] = float(np.max(np.linalg.norm(
            np.array([r["corners_px"] for r in good]) - np.mean([r["corners_px"] for r in good], axis=0), axis=2)))
        if result["ambiguous_frames"]:
            reasons.append("planar_pose_candidates_not_well_separated")
        if result["max_reprojection_rms_px"] > LIMITS["reprojection_px"]:
            reasons.append("high_reprojection_error")
        for key in ("best_ippe", "iterative"):
            if key in result and (result[key]["max_translation_from_mean_mm"] > LIMITS["vision_translation_mm"]
                                  or result[key]["max_rotation_from_mean_deg"] > LIMITS["vision_rotation_deg"]):
                reasons.append(key + "_unstable")
    else:
        reasons.append("no_valid_board_poses")
    result["status"] = "REVIEW" if reasons else "STABLE_BURST"
    result["calibration_validated"] = False
    return result


def capture(args):
    from vision.camera import GeminiCamera
    from xarm_grasp.robot import Robot
    config = load(args.config)
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    path = Path(args.output) / (args.label + "_" + suffix)
    path.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "label": args.label, "created_utc": utc_now(),
              "board": [args.cols, args.rows, args.square_mm], "thresholds": LIMITS,
              "robot_ip": config["robot"]["ip"], "requested_frames": args.frames,
              "timing_note": "Host read/capture intervals only, not exposure-synchronized hardware timestamps.",
              "note": "One burst is one stationary pose. Never count its frames as independent hand-eye views.",
              "frames": []}
    write(path / "report.json", report)
    print(f"Output: {path.resolve()}", flush=True)
    reference = None
    try:
        with GeminiCamera(config["camera"]) as camera, Robot(config["robot"]) as robot:
            points = object_points(args.cols, args.rows, args.square_mm)
            for index in range(1, args.frames + 1):
                record = {"index": index}
                report["frames"].append(record)
                record["robot_before"] = snapshot(robot)
                record["host_capture_start_monotonic_ns"] = time.monotonic_ns()
                frame = camera.capture()
                record["host_capture_end_monotonic_ns"] = time.monotonic_ns()
                record["host_capture_end_utc"] = utc_now()
                record["robot_after"] = snapshot(robot)
                record["camera"] = {k: v for k, v in frame.items() if k not in ("bgr", "depth")}
                raw = f"frame_{index:03d}_raw.png"
                annotated = f"frame_{index:03d}_annotated.png"
                # Save BEFORE drawing. PNG keeps the original pixel values.
                if not cv2.imwrite(str(path / raw), frame["bgr"]):
                    raise IOError("Could not save raw PNG")
                record["raw_image"] = raw
                drawn = frame["bgr"].copy()
                try:
                    gray = cv2.cvtColor(frame["bgr"], cv2.COLOR_BGR2GRAY)
                    found, corners = cv2.findChessboardCornersSB(gray, (args.cols, args.rows))
                    if not found:
                        raise ValueError("Chessboard not found")
                    record["detected_corners_px"] = corners.reshape(-1, 2).tolist()
                    corners, reversed_order = normalize_corner_order(corners, reference)
                    if reference is None:
                        reference = corners.copy()
                    record["corner_order_reversed_within_burst"] = reversed_order
                    record["corners_px"] = corners.reshape(-1, 2).tolist()
                    fx, fy, cx, cy = frame["intrinsics"]
                    k = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.]], float)
                    record["vision"] = estimate_board(points, corners, k, np.array(frame["distortion"], float))
                    cv2.drawChessboardCorners(drawn, (args.cols, args.rows), corners, True)
                    origin = tuple(np.rint(corners[0, 0]).astype(int))
                    cv2.circle(drawn, origin, 7, (255, 0, 255), 2)
                    cv2.putText(drawn, "O", (origin[0]+9, origin[1]), cv2.FONT_HERSHEY_SIMPLEX,
                                0.5, (255, 0, 255), 1)
                    c = record["vision"]
                    text = f'{index}/{args.frames} error={c["best_ippe"]["reprojection_rms_px"]:.3f}px ambiguous={c["ambiguity"]["flag"]}'
                except (ValueError, cv2.error) as exc:
                    record["vision_error"] = str(exc)
                    text = f"{index}/{args.frames} REVIEW: {exc}"
                cv2.putText(drawn, text, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
                if not cv2.imwrite(str(path / annotated), drawn):
                    raise IOError("Could not save annotated PNG")
                record["annotated_image"] = annotated
                write(path / "report.json", report)
                print(text, flush=True)
                if args.interval and index < args.frames:
                    time.sleep(args.interval)
    except BaseException as exc:
        report["capture_error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["summary"] = summarize(report["frames"])
        if "capture_error" in report:
            report["summary"]["status"] = "INCOMPLETE"
        write(path / "report.json", report)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)
    return report


def compare(root):
    sessions = []
    for file in sorted(Path(root).glob("*/report.json")):
        r = load(file)
        summary = r.get("summary", {})
        if "best_ippe" not in summary or "robot" not in summary:
            continue
        frames = [f for f in r["frames"] if "vision" in f]
        f = frames[0]
        signature = {"board": r["board"], "robot_ip": r["robot_ip"],
                     "camera": {k: f["camera"][k] for k in ("serial", "intrinsics", "distortion", "color_profile")},
                     "tcp_offset": f["robot_before"]["tcp_offset"],
                     "world_offset": f["robot_before"]["world_offset"]}
        sessions.append({"label": r["label"], "file": str(file.resolve()),
                         "summary": summary, "signature": signature})
    pairs = []
    for i, left in enumerate(sessions):
        for right in sessions[i+1:]:
            if left["signature"] != right["signature"]:
                pairs.append({"left": left["file"], "right": right["file"], "status": "INCOMPATIBLE_METADATA"})
                continue
            ls, rs = left["summary"], right["summary"]
            arm_delta = pose_delta(ls["robot"]["mean_T_base_flange"], rs["robot"]["mean_T_base_flange"])
            visual_delta = pose_delta(ls["best_ippe"]["mean_pose"], rs["best_ippe"]["mean_pose"])
            jd = np.array(ls["robot"]["mean_joint_angles_deg"]) - np.array(rs["robot"]["mean_joint_angles_deg"])
            pair = {"left": left["file"], "right": right["file"],
                    "robot_pose_difference": arm_delta, "vision_pose_difference": visual_delta,
                    "max_joint_difference_deg": float(np.max(np.abs((jd+180) % 360-180))),
                    "relative_rotation_angle_mismatch_deg": abs(arm_delta["rotation_deg"]-visual_delta["rotation_deg"]),
                    "review_required": ls["status"] != "STABLE_BURST" or rs["status"] != "STABLE_BURST"}
            if "iterative" in ls and "iterative" in rs:
                d = pose_delta(ls["iterative"]["mean_pose"], rs["iterative"]["mean_pose"])
                pair["iterative_rotation_angle_mismatch_deg"] = abs(arm_delta["rotation_deg"] - d["rotation_deg"])
            pairs.append(pair)
    return {"session_count": len(sessions), "sessions": sessions, "pairs": pairs,
            "note": "No hand-eye fit. Board must stay fixed. Across bursts verify corner origin/order in annotated images. "
                    "Translation differences are useful for return-to-same-pose tests; do not equate arm and vision translation magnitudes at different poses. "
                    "REVIEW/INCOMPLETE bursts are retained for diagnosis, never certified as calibration views."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("capture", help="Capture one stationary burst; no robot motion")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--output", default=str(DATA_DIR / "probe_v1"))
    p.add_argument("--label", required=True, help="e.g. A1, B1, C1, A2; letters/digits/_/- only")
    p.add_argument("--frames", type=int, default=20)
    p.add_argument("--cols", type=int, default=9)
    p.add_argument("--rows", type=int, default=6)
    p.add_argument("--square-mm", type=float, default=24.0)
    p.add_argument("--interval", type=float, default=0.1)
    p = sub.add_parser("compare", help="Offline comparison of captured bursts")
    p.add_argument("--input", default=str(DATA_DIR / "probe_v1"))
    args = parser.parse_args()
    if args.command == "capture":
        if (not args.label or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in args.label)
                or not 5 <= args.frames <= 200 or min(args.cols, args.rows) < 3
                or not np.isfinite(args.square_mm) or args.square_mm <= 0
                or not np.isfinite(args.interval) or not 0 <= args.interval <= 5):
            parser.error("Invalid label, frames (5..200), board dimensions, or interval (0..5 seconds)")
    try:
        if args.command == "capture":
            capture(args)
        else:
            result = compare(args.input)
            if result["session_count"] < 2:
                raise ValueError("Need at least two bursts with detected board poses")
            output = Path(args.input) / ("comparison_" + uuid.uuid4().hex[:8] + ".json")
            write(output, result)
            print(f"Saved {output.resolve()}")
            for session in result["sessions"]:
                print(session["label"], session["summary"]["status"], session["file"])
            for pair in result["pairs"]:
                print(json.dumps(pair, ensure_ascii=False))
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f"Stopped: {exc}\n")


if __name__ == "__main__":
    main()
