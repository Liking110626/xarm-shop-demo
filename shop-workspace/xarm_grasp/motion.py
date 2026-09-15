"""Fixed-orientation Y/X/Z grasp planning and robot motion orchestration."""
import json
import time
import shutil
import uuid
from pathlib import Path
import numpy as np
from .coordinates import vector, pose_matrix, tcp_to_base, check_workspace
from .config import load, write, validate_observation
from vision.yolo import acquire_product, TargetNotFoundError


def make_tool_axis_plan_from_tcp(point_tcp, tcp_pose, grasp_depth_offset_mm, motion, bounds):
    """Plan from TCP XYZ mm. The base pose is used for workspace/IK preflight."""
    raw = vector(point_tcp, 3, "TCP target point")
    pose = vector(tcp_pose, 6, "TCP pose")
    offset = float(grasp_depth_offset_mm)
    if not np.isfinite(offset) or offset < 0:
        raise ValueError("grasp_depth_offset_mm must be a non-negative measured value")

    axis_index = {"x": 0, "y": 1, "z": 2}
    roles = [motion["lateral_axis"], motion["vertical_axis"], motion["depth_axis"]]
    if set(roles) != set(axis_index):
        raise ValueError("lateral_axis, vertical_axis and depth_axis must use x/y/z exactly once")
    lateral_i, vertical_i, depth_i = (axis_index[name] for name in roles)
    approach_sign = motion.get('approach_axis_sign')
    lift_sign = motion.get('lift_axis_sign')
    if type(approach_sign) is not int or approach_sign not in (-1, 1):
        raise ValueError('approach_axis_sign must be -1 or 1')
    if type(lift_sign) is not int or lift_sign not in (-1, 1):
        raise ValueError('lift_axis_sign must be -1 or 1')

    rotation = pose_matrix(pose)[:3, :3]
    lateral = float(raw[lateral_i])
    vertical = float(raw[vertical_i])
    depth = float(raw[depth_i])
    if abs(lateral) > motion["max_alignment_mm"] or abs(vertical) > motion["max_alignment_mm"]:
        raise ValueError(f"Required lateral/vertical alignment exceeds limit: {[lateral, vertical]}")
    signed_depth = depth * approach_sign
    if not motion["min_approach_mm"] <= signed_depth <= motion["max_approach_mm"]:
        sign_name = "+" if approach_sign > 0 else "-"
        raise ValueError(
            f"Target must be in configured tool {roles[2].upper()}{sign_name} approach direction; "
            f"depth={depth:.3f} mm"
        )
    approach = depth + approach_sign * offset
    deadband = motion["axis_deadband_mm"]
    if abs(lateral) <= deadband:
        lateral = 0.0
    if abs(vertical) <= deadband:
        vertical = 0.0

    up_component = float(rotation[:, vertical_i] @ np.array([0.0, 0.0, 1.0]))
    lift_up_component = lift_sign * up_component
    if lift_up_component < motion["vertical_axis_min_up_component"]:
        sign_name = "+" if lift_sign > 0 else "-"
        raise ValueError(
            f"Configured tool {roles[1].upper()}{sign_name} lift direction is not sufficiently upward "
            "at the observation pose"
        )
    lift = lift_sign * float(motion["lift_mm"])

    def axis_delta(index, distance):
        delta = np.zeros(3)
        delta[index] = distance
        return delta

    commands = [
        ("align_lateral", roles[0], axis_delta(lateral_i, lateral)),
        ("align_vertical", roles[1], axis_delta(vertical_i, vertical)),
        ("approach_depth", roles[2], axis_delta(depth_i, approach)),
        ("lift", roles[1], axis_delta(vertical_i, lift)),
        ("pullout", roles[2], axis_delta(depth_i, -approach)),
    ]
    xyz = pose[:3].copy()
    steps = []
    for name, axis, delta in commands:
        xyz = xyz + rotation @ delta
        target_pose = np.r_[xyz, pose[3:]].tolist()
        check_workspace(target_pose, bounds)
        steps.append({"name": name, "axis": axis, "delta_tool_mm": delta.tolist(),
                      "target_pose": target_pose})
    desired = raw.copy()
    desired[depth_i] = approach
    return {"axis_roles": {"lateral": roles[0], "vertical": roles[1], "depth": roles[2]},
            "axis_signs": {"approach": approach_sign, "lift": lift_sign},
            "target_delta_tool_mm": desired.tolist(), "steps": steps}


def execute_grasp_plan(robot, plan, product, config):
    """Execute a preflighted plan: open, Y/X/Z, close, X- lift, Z- pullout."""
    steps = {step['name']: step for step in plan['steps']}
    robot.activate_gripper()
    robot.gripper(product['open_position'])
    for name in ('align_lateral', 'align_vertical', 'approach_depth'):
        robot.move_tool(steps[name]['delta_tool_mm'], config['workspace_mm'], config['tool_motion'])
    robot.gripper(product['close_position'])
    robot.verify_grasp()
    robot.move_tool(steps['lift']['delta_tool_mm'], config['workspace_mm'], config['tool_motion'])
    robot.verify_grasp()
    robot.move_tool(steps['pullout']['delta_tool_mm'], config['workspace_mm'], config['tool_motion'])
    robot.verify_grasp()


def grasp_from_observation(camera, robot, model, product, item_name, config, output,
                           receipt_report=None, plan_only=False):
    output = Path(output)
    observations = [('primary', config['grasp_observation'])]
    if config.get('grasp_observation_fallback') is not None:
        observations.append(('fallback', config['grasp_observation_fallback']))
    for label, observation in observations:
        validate_observation(observation, f'{label} grasp observation')
    # An unsuccessful new search must never leave an old step-4 plan executable.
    write(output / 'result.json', {'status': 'observation_search_started', 'item': item_name})
    search_dir = output / 'observations' / uuid.uuid4().hex[:12]
    attempts = []
    for index, (label, observation) in enumerate(observations, start=1):
        attempt_output = search_dir / f'{index:02d}_{label}'
        print(f'YOLO observation {index}/{len(observations)}: {label}', flush=True)
        robot.observe(observation, config['workspace_mm'])
        time.sleep(1)
        tcp_offset = robot.offset()
        pose_before = robot.pose()
        missing = None
        try:
            _, report = acquire_product(camera, model, product, item_name, config, attempt_output,
                                        receipt_report=receipt_report, tcp_offset=tcp_offset)
        except TargetNotFoundError as exc:
            missing = exc
        finally:
            # Keep root-level evidence compatible with --step 5 and offline replay.
            # Each viewpoint has its own directory, including failed observations.
            for filename in ('color.png', 'depth_mm.npy', 'frame.json', 'detection.jpg', 'result.json'):
                source = attempt_output / filename
                if source.is_file():
                    shutil.copy2(source, output / filename)
        pose_after = robot.pose()
        if not np.allclose(pose_before, pose_after, atol=0.1, rtol=0):
            raise RuntimeError('Robot moved during capture/inference')
        attempts.append({'index': index, 'label': label, 'observation': observation,
                         'tcp_at_capture': pose_after,
                         'evidence_dir': attempt_output.relative_to(output).as_posix(),
                         'status': 'target_not_found' if missing else 'target_found'})
        if missing is None:
            break
        if index == len(observations):
            failure = load(attempt_output / 'result.json')
            failure.update(observation_attempts=attempts)
            write(output / 'result.json', failure)
            raise TargetNotFoundError(
                f'Expected exactly one target, detected 0 at all {len(observations)} observation pose(s); '
                f'evidence: {search_dir.resolve()}') from missing
        print('Target not found; moving directly to fallback observation', flush=True)
    report.update(observation_used=label, observation_index=index,
                  observation_attempts=attempts)
    target = tcp_to_base(report['tcp_point_mm'], pose_after)
    plan = make_tool_axis_plan_from_tcp(report['tcp_point_mm'], pose_after, product['grasp_depth_offset_mm'],
                               config['tool_motion'], config['workspace_mm'])
    for step in plan['steps']:
        robot.preflight(step['target_pose'], config['workspace_mm'])
    report.update(base_point_mm=target.tolist(), tcp_at_capture=pose_after,
                  tool_axis_plan=plan,
                  status='planned_no_grasp' if plan_only else 'planned')
    write(Path(output) / 'result.json', report)
    write(attempt_output / 'result.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if plan_only:
        return report

    execute_grasp_plan(robot, plan, product, config)
    report['status'] = 'holding_object_after_pullout'
    write(Path(output) / 'result.json', report)
    return report
