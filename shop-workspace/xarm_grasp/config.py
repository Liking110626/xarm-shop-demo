"""Configuration, JSON output and execution validation. No hardware access."""
import json
from pathlib import Path
import numpy as np
from .coordinates import vector, transform

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config.json"


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
