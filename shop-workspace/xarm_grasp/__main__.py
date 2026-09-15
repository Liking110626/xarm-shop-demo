import argparse
import json
from pathlib import Path
import time
import numpy as np
from .geometry import vector, transform, depth_at, deproject, base_point, make_tool_axis_plan


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def resolve_entry(config, name):
    matches = [(key, p) for key, p in config['products'].items()
               if name == key or name in p.get('aliases', [])]
    if len(matches) != 1:
        raise ValueError(f'Unknown or ambiguous product: {name}')
    return matches[0]


def resolve(config, name):
    return resolve_entry(config, name)[1]


def positive_number(value, name, allow_zero=False):
    if not isinstance(value, (float, int)) or not np.isfinite(value):
        raise ValueError(f'Invalid {name}')
    if value < 0 if allow_zero else value <= 0:
        raise ValueError(f'Invalid {name}')
    return float(value)


def validate_observation(observation, name='observation'):
    if not isinstance(observation, dict) or observation.get('type') not in ('joint', 'tcp'):
        raise ValueError(f'{name}.type must be joint or tcp')
    vector(observation.get('values'), 6, name)


def validate_common_execution(config):
    if config.get('motion_enabled') is not True:
        raise ValueError('motion_enabled is false; physical setup must be validated first')
    calibration = config['calibration']
    if calibration.get('validated') is not True or not calibration.get('camera_serial'):
        raise ValueError('Validated hand-eye calibration with camera serial is required')
    transform(calibration['T_flange_camera'])
    vector(config['robot']['tcp_offset'], 6, 'tcp_offset')
    validate_observation(config.get('initial_pose'), 'initial pose')
    validate_observation(config.get('grasp_observation'), 'grasp observation')
    for key in ('tcp_speed_mm_s', 'tcp_acc_mm_s2', 'joint_speed_deg_s', 'joint_acc_deg_s2'):
        positive_number(config['robot'][key], key)
    motion = config['tool_motion']
    axes = [motion.get('lateral_axis'), motion.get('vertical_axis'), motion.get('depth_axis')]
    if set(axes) != {'x', 'y', 'z'}:
        raise ValueError('tool motion axes must use x/y/z exactly once')
    for key in ('approach_axis_sign', 'lift_axis_sign'):
        if type(motion.get(key)) is not int or motion[key] not in (-1, 1):
            raise ValueError(f'tool_motion.{key} must be -1 or 1')
    for key in ('lift_mm', 'axis_deadband_mm', 'max_alignment_mm', 'min_approach_mm',
                'max_approach_mm', 'vertical_axis_min_up_component',
                'position_tolerance_mm', 'orientation_tolerance_deg'):
        positive_number(motion[key], f'tool_motion.{key}', allow_zero=(key == 'axis_deadband_mm'))
    if motion['min_approach_mm'] >= motion['max_approach_mm']:
        raise ValueError('min_approach_mm must be less than max_approach_mm')
    if not 0 < motion['vertical_axis_min_up_component'] <= 1:
        raise ValueError('vertical_axis_min_up_component must be in (0, 1]')
    b = np.asarray(config['workspace_mm'], float)
    if b.shape != (3, 2) or not np.isfinite(b).all() or np.any(b[:, 0] >= b[:, 1]):
        raise ValueError('Set workspace_mm to measured operating limits')


def validate_product_execution(product):
    positive_number(product['grasp_depth_offset_mm'], 'grasp_depth_offset_mm', allow_zero=True)
    for key in ('open_position', 'close_position'):
        if type(product[key]) is not int or not 0 <= product[key] <= 255:
            raise ValueError(f'{key} must be a raw position in 0..255')
    if product['open_position'] >= product['close_position']:
        raise ValueError('close_position must exceed open_position')


def validate_execution(config, product):
    validate_common_execution(config)
    validate_product_execution(product)


def detect(model, frame, product, threshold):
    result = model.predict(frame['bgr'], conf=threshold, verbose=False)[0]
    candidates = []
    for box in result.boxes:
        if int(box.cls.item()) == product['class_id']:
            bbox = box.xyxy[0].cpu().numpy()
            uv = (bbox[:2] + bbox[2:]) / 2
            candidates.append({'bbox': bbox.tolist(), 'uv': uv.tolist(),
                               'confidence': float(box.conf.item())})
    if len(candidates) != 1:
        raise RuntimeError(f'Expected exactly one target, detected {len(candidates)}')
    return candidates[0]


def load_model(config_path, product):
    model_path = (config_path.parent / product['model']).resolve()
    if not model_path.is_file():
        raise FileNotFoundError(f'Trained YOLO weights required: {model_path}')
    from ultralytics import YOLO
    model = YOLO(str(model_path))
    if type(product['class_id']) is not int or product['class_id'] not in model.names:
        raise ValueError('class_id does not exist in model.names')
    return model


def recognize_receipt_frame(frame, config, output):
    from .receipt import recognize_receipt
    return recognize_receipt(frame['bgr'], config, Path(output) / 'receipt')


def recognize_receipt_file(image_path, config, output):
    import cv2
    path = Path(image_path).expanduser().resolve()
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f'Could not read receipt image: {path}')
    from .receipt import recognize_receipt
    return recognize_receipt(image, config, Path(output) / 'receipt')


def acquire_product(camera, model, product, item_name, config, output, receipt_report=None):
    frame = camera.capture()
    detection = detect(model, frame, product, config['confidence'])
    z = depth_at(frame['depth'], detection['uv'], **config['depth'])
    point = deproject(detection['uv'], z, frame['intrinsics'], frame['distortion'])
    report = {'item': item_name, 'detection': detection,
              'camera_point_mm': point.tolist(), 'camera_serial': frame['serial'],
              'status': 'vision_only'}
    if receipt_report is not None:
        report['receipt'] = receipt_report
    import cv2
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    img = frame['bgr'].copy()
    x1, y1, x2, y2 = map(int, detection['bbox'])
    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
    if not cv2.imwrite(str(output / 'detection.jpg'), img):
        raise IOError('Could not save detection image')
    np.save(output / 'depth_mm.npy', frame['depth'])
    write(output / 'result.json', report)
    return point, report


def grasp_from_observation(camera, robot, model, product, item_name, config, output,
                           receipt_report=None, plan_only=False):
    robot.observe(config['grasp_observation'], config['workspace_mm'])
    time.sleep(1)
    pose_before = robot.pose()
    point, report = acquire_product(camera, model, product, item_name, config, output,
                                    receipt_report=receipt_report)
    pose_after = robot.pose()
    if not np.allclose(pose_before, pose_after, atol=0.1):
        raise RuntimeError('Robot moved during capture/inference')
    target = base_point(point, pose_after, robot.offset(), config['calibration']['T_flange_camera'])
    plan = make_tool_axis_plan(target, pose_after, product['grasp_depth_offset_mm'],
                               config['tool_motion'], config['workspace_mm'])
    for step in plan['steps']:
        robot.preflight(step['target_pose'], config['workspace_mm'])
    report.update(base_point_mm=target.tolist(), tcp_at_capture=pose_after,
                  tool_axis_plan=plan,
                  status='planned_no_grasp' if plan_only else 'planned')
    write(Path(output) / 'result.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if plan_only:
        return report

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
    report['status'] = 'holding_object_after_pullout'
    write(Path(output) / 'result.json', report)


def run(args):
    from .camera import GeminiCamera
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
                                        output, receipt_report=receipt_report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    validate_common_execution(config)
    if item_name is None:
        receipt_config = config.get('receipt')
        if not isinstance(receipt_config, dict):
            raise ValueError('receipt configuration is required')
        validate_observation(receipt_config.get('observation'), 'receipt observation')
        from .receipt import validate_receipt_settings
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
            grasp_from_observation(camera, robot, model, product, canonical_name, config,
                                   output, receipt_report=receipt_report,
                                   plan_only=args.plan_only)


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






