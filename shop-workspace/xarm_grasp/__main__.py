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
from .motion import grasp_from_observation


def run(args):
    from vision.camera import GeminiCamera
    from .robot import Robot
    config_path = Path(args.config).resolve()
    config = load(config_path)
    output = Path(args.output)
    item_name = args.item
    receipt_report = None

    if args.plan_only and not args.execute:
        raise ValueError('--plan-only requires --execute because the wrist camera must move to the observation pose')

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
            robot.observe(config['initial_pose'], config['workspace_mm'])
            if item_name is None:
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
                robot.observe(config['initial_pose'], config['workspace_mm'])
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
    parser.add_argument('--output', default='runs/latest')
    args = parser.parse_args()
    try:
        run(args)
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f'Stopped: {exc}\n')


if __name__ == '__main__':
    main()
