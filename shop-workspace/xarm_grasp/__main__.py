"""Command-line entry and receipt-driven grasp workflow."""
import argparse
import json
import time
from pathlib import Path
import numpy as np
from .config import (load, write, resolve_entry, validate_common_execution,
                     validate_observation, validate_product_execution)
from vision.ocr import recognize_receipt_frame, recognize_receipt_file, validate_receipt_settings
from vision.yolo import load_model, acquire_product
from .motion import grasp_from_observation, execute_grasp_plan, make_tool_axis_plan_from_tcp


STEP_LABELS = (
    '移动到 initial_pose',
    '移动到小票识别位并执行 OCR',
    '返回 initial_pose',
    '移动到商品预抓取位并执行 YOLO/轨迹预检',
    '抓取对应商品',
    '携带商品返回 initial_pose',
)


def confirm_step(number):
    """Require an explicit operator confirmation before one physical test stage."""
    label = STEP_LABELS[number - 1]
    answer = input(
        f'\n[步骤 {number}/6] {label}\n'
        '确认现场安全后按 Enter 执行；输入 q 后按 Enter 停止：'
    ).strip().lower()
    if answer == 'q':
        raise KeyboardInterrupt(f'operator stopped before step {number}')
    if answer:
        raise ValueError('只接受 Enter（继续）或 q（停止）')


def step_done(number, detail=None):
    suffix = '' if detail is None else f'：{detail}'
    print(f'[步骤 {number}/6 完成] {STEP_LABELS[number - 1]}{suffix}', flush=True)


def load_optional_receipt(output):
    path = Path(output) / 'receipt' / 'receipt_result.json'
    return load(path) if path.is_file() else None


def resolve_isolated_item(args, config, output):
    """Resolve step 4's item from --item or an earlier step 2 in the same output dir."""
    if args.item is not None:
        return args.item, None
    receipt_report = load_optional_receipt(output)
    if receipt_report is None or not receipt_report.get('selected_item'):
        raise ValueError(
            'step 4 requires --item, or a completed step 2 using the same --output directory')
    return receipt_report['selected_item'], receipt_report


def run_isolated_step(args, config_path, config, output, number):
    """Execute exactly one major physical workflow stage, then disconnect."""
    from .robot import Robot

    validate_common_execution(config)
    if number in (1, 3, 6):
        with Robot(config['robot']) as robot:
            robot.enable()
            robot.observe(config['initial_pose'], config['workspace_mm'])
        if number == 6:
            result_path = output / 'result.json'
            if result_path.is_file():
                report = load(result_path)
                report['status'] = 'holding_object_at_initial_pose'
                write(result_path, report)
        step_done(number)
        return

    if number == 2:
        from vision.camera import GeminiCamera
        receipt_config = config.get('receipt')
        if not isinstance(receipt_config, dict):
            raise ValueError('receipt configuration is required')
        validate_observation(receipt_config.get('observation'), 'receipt observation')
        validate_receipt_settings(config)
        with GeminiCamera(config['camera']) as camera:
            if camera.serial != config['calibration']['camera_serial']:
                raise ValueError('Connected camera differs from calibrated camera')
            camera.capture()
            with Robot(config['robot']) as robot:
                robot.enable()
                robot.observe(receipt_config['observation'], config['workspace_mm'])
                time.sleep(1)
                pose_before = robot.pose()
                report = recognize_receipt_frame(camera.capture(), config, output)
                pose_after = robot.pose()
                if not np.allclose(pose_before, pose_after, atol=0.1):
                    raise RuntimeError('Robot moved during receipt capture/OCR')
        report['status'] = 'step_2_receipt_resolved'
        write(output / 'receipt' / 'receipt_result.json', report)
        step_done(2, f"OCR 商品 = {report['selected_item']}")
        return

    if number == 4:
        from vision.camera import GeminiCamera
        item_name, receipt_report = resolve_isolated_item(args, config, output)
        canonical_name, product = resolve_entry(config, item_name)
        validate_product_execution(product)
        model = load_model(config_path, product)
        with GeminiCamera(config['camera']) as camera:
            if camera.serial != config['calibration']['camera_serial']:
                raise ValueError('Connected camera differs from calibrated camera')
            camera.capture()
            with Robot(config['robot']) as robot:
                robot.enable()
                report = grasp_from_observation(
                    camera, robot, model, product, canonical_name, config, output,
                    receipt_report=receipt_report, plan_only=True)
        report['status'] = 'step_4_grasp_planned'
        write(output / 'result.json', report)
        step_done(4, f'YOLO 商品 = {canonical_name}；抓取轨迹已预检，尚未抓取')
        return

    # Step 5 deliberately does not open the camera or return to the observation pose.
    # It may only continue from a saved step-4 plan while the robot is still there.
    result_path = output / 'result.json'
    if not result_path.is_file():
        raise ValueError('step 5 requires a completed step 4 using the same --output directory')
    report = load(result_path)
    if report.get('status') != 'step_4_grasp_planned':
        raise ValueError('step 5 requires result.json produced by isolated step 4')
    canonical_name, product = resolve_entry(config, report.get('item'))
    validate_product_execution(product)
    capture_pose = report.get('tcp_at_capture')
    plan = make_tool_axis_plan_from_tcp(
        report.get('tcp_point_mm'), capture_pose, product['grasp_depth_offset_mm'],
        config['tool_motion'], config['workspace_mm'])
    with Robot(config['robot']) as robot:
        robot.enable()
        current_pose = robot.pose()
        if not np.allclose(current_pose, capture_pose, atol=0.1):
            raise RuntimeError(
                'Robot is no longer at the step-4 capture pose; rerun step 4 before step 5')
        for plan_step in plan['steps']:
            robot.preflight(plan_step['target_pose'], config['workspace_mm'])
        execute_grasp_plan(robot, plan, product, config)
    report['tool_axis_plan'] = plan
    report['status'] = 'holding_object_after_pullout'
    write(result_path, report)
    step_done(5, f'已确认夹持 {canonical_name}')


def run(args):
    from vision.camera import GeminiCamera
    from .robot import Robot
    config_path = Path(args.config).resolve()
    config = load(config_path)
    output = Path(args.output)
    item_name = args.item
    receipt_report = None
    step_test = getattr(args, 'step_test', False)
    isolated_step = getattr(args, 'step', None)

    if args.plan_only and not args.execute:
        raise ValueError('--plan-only requires --execute because the wrist camera must move to the observation pose')
    if step_test and not args.execute:
        raise ValueError('--step-test requires --execute')
    if step_test and args.plan_only:
        raise ValueError('--step-test and --plan-only cannot be used together')
    if step_test and (args.item is not None or args.receipt_image is not None):
        raise ValueError('--step-test uses the wrist camera and receipt OCR; do not pass --item or --receipt-image')
    if isolated_step is not None:
        if not args.execute:
            raise ValueError('--step requires --execute')
        if args.plan_only:
            raise ValueError('--step and --plan-only cannot be used together')
        if args.receipt_image is not None:
            raise ValueError('--step uses the wrist camera; do not pass --receipt-image')
        if args.item is not None and isolated_step != 4:
            raise ValueError('--item is only valid with --step 4')
        run_isolated_step(args, config_path, config, output, isolated_step)
        return

    if args.receipt_image:
        receipt_report = recognize_receipt_file(args.receipt_image, config, output)
        item_name = receipt_report['selected_item']

    if not args.execute:
        if args.item is None:
            if receipt_report is None:
                with GeminiCamera(config['camera']) as camera:
                    receipt_report = recognize_receipt_frame(camera.capture(), config, output)
            receipt_report['status'] = 'receipt_resolved_no_motion'
            write(output / 'receipt' / 'receipt_result.json', receipt_report)
            print(json.dumps(receipt_report, ensure_ascii=False, indent=2))
            return
        canonical_name, product = resolve_entry(config, item_name)
        model = load_model(config_path, product)
        with GeminiCamera(config['camera']) as camera:
            _, report = acquire_product(camera, model, product, canonical_name, config,
                                        output, receipt_report=receipt_report, include_tcp=False)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    validate_common_execution(config)
    if item_name is None:
        receipt_config = config.get('receipt')
        if not isinstance(receipt_config, dict):
            raise ValueError('receipt configuration is required')
        validate_observation(receipt_config.get('observation'), 'receipt observation')
        validate_receipt_settings(config)
        for candidate in config['products'].values():
            validate_product_execution(candidate)
    if item_name is not None:
        canonical_name, product = resolve_entry(config, item_name)
        validate_product_execution(product)
        model = load_model(config_path, product)

    with GeminiCamera(config['camera']) as camera:
        if camera.serial != config['calibration']['camera_serial']:
            raise ValueError('Connected camera differs from calibrated camera')
        camera.capture()
        with Robot(config['robot']) as robot:
            robot.enable()
            if step_test:
                confirm_step(1)
            robot.observe(config['initial_pose'], config['workspace_mm'])
            if step_test:
                step_done(1)
            if item_name is None:
                if step_test:
                    confirm_step(2)
                robot.observe(receipt_config['observation'], config['workspace_mm'])
                time.sleep(1)
                pose_before = robot.pose()
                receipt_report = recognize_receipt_frame(camera.capture(), config, output)
                pose_after = robot.pose()
                if not np.allclose(pose_before, pose_after, atol=0.1):
                    raise RuntimeError('Robot moved during receipt capture/OCR')
                item_name = receipt_report['selected_item']
                canonical_name, product = resolve_entry(config, item_name)
                validate_product_execution(product)
                model = load_model(config_path, product)
                if step_test:
                    step_done(2, f'OCR 商品 = {canonical_name}')
                    confirm_step(3)
                robot.observe(config['initial_pose'], config['workspace_mm'])
                if step_test:
                    step_done(3)
            if step_test:
                confirm_step(4)
                report = grasp_from_observation(
                    camera, robot, model, product, canonical_name, config, output,
                    receipt_report=receipt_report, plan_only=True)
                step_done(4, f'YOLO 商品 = {canonical_name}；抓取轨迹已预检，尚未抓取')
                confirm_step(5)
                execute_grasp_plan(robot, report['tool_axis_plan'], product, config)
                report['status'] = 'holding_object_after_pullout'
                write(output / 'result.json', report)
                step_done(5, f'已确认夹持 {canonical_name}')
                confirm_step(6)
                robot.observe(config['initial_pose'], config['workspace_mm'])
                report['status'] = 'holding_object_at_initial_pose'
                write(output / 'result.json', report)
                step_done(6)
                return
            report = grasp_from_observation(
                camera, robot, model, product, canonical_name, config, output,
                receipt_report=receipt_report, plan_only=args.plan_only)
            robot.observe(config['initial_pose'], config['workspace_mm'])
            report['status'] = (
                'planned_no_grasp_returned_to_initial' if args.plan_only
                else 'holding_object_at_initial_pose')
            write(output / 'result.json', report)


def main():
    parser = argparse.ArgumentParser(
        description='Receipt-driven xArm 6 / Gemini 336 / Robotiq 2F-85 grasping')
    parser.add_argument('--config', default=str(Path(__file__).resolve().parents[1] / 'config.json'))
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--item', help='Bypass receipt OCR and directly select a product')
    source.add_argument('--receipt-image', help='Use an existing receipt image instead of the wrist camera')
    parser.add_argument('--execute', action='store_true', help='Physically move arm and gripper')
    parser.add_argument('--plan-only', action='store_true',
                        help='Move through safe/observation poses and compute the grasp plan; stop before gripper or XYZ grasp motion')
    step_mode = parser.add_mutually_exclusive_group()
    step_mode.add_argument('--step-test', action='store_true',
                           help='Run the complete receipt-driven physical workflow as six operator-confirmed steps')
    step_mode.add_argument('--step', type=int, choices=range(1, 7), metavar='N',
                           help='Execute only physical workflow step N (1..6), then exit')
    parser.add_argument('--output', default='runs/latest')
    args = parser.parse_args()
    try:
        run(args)
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f'Stopped: {exc}\n')


if __name__ == '__main__':
    main()
