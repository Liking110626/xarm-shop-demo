"""Read-only chessboard sample capture and offline eye-in-hand solve."""
import argparse
from pathlib import Path
import numpy as np
from .__main__ import load, write
from .geometry import pose_matrix, transform


def solve(samples):
    import cv2
    if len(samples) < 12:
        raise ValueError('Collect at least 12 stationary views with rotations about multiple axes')
    serials = {s['camera_serial'] for s in samples}
    if len(serials) != 1:
        raise ValueError('Samples came from different cameras')
    offsets = [np.asarray(s['tcp_offset'], float) for s in samples if 'tcp_offset' in s]
    if offsets and (len(offsets) != len(samples) or
                    any(not np.allclose(offsets[0], value, atol=0.05) for value in offsets[1:])):
        raise ValueError('TCP offset changed or is missing within the calibration samples')
    a = [transform(s['T_base_flange']) for s in samples]
    b = [transform(s['T_camera_board']) for s in samples]
    rotations = np.array([cv2.Rodrigues(a[0][:3, :3].T @ t[:3, :3])[0].ravel() for t in a[1:]])
    if np.linalg.svd(rotations, compute_uv=False)[1] < 0.2:
        raise ValueError('Insufficient independent rotation axes for hand-eye calibration')
    r, t = cv2.calibrateHandEye([m[:3, :3] for m in a], [m[:3, 3] for m in a],
                              [m[:3, :3] for m in b], [m[:3, 3] for m in b],
                              method=cv2.CALIB_HAND_EYE_PARK)
    x = np.eye(4)
    x[:3, :3], x[:3, 3] = r, t.ravel()
    transform(x)
    boards = [aa @ x @ bb for aa, bb in zip(a, b)]
    center = np.mean([m[:3, 3] for m in boards], axis=0)
    trans_error = max(np.linalg.norm(m[:3, 3] - center) for m in boards)
    rot_error = max(np.rad2deg(np.linalg.norm(cv2.Rodrigues(boards[0][:3, :3].T @ m[:3, :3])[0])) for m in boards)
    if trans_error > 5 or rot_error > 2:
        raise ValueError(f'Inconsistent fixed board: {trans_error:.2f} mm / {rot_error:.2f} deg')
    result = {'validated': False, 'camera_serial': samples[0]['camera_serial'],
              'T_flange_camera': x.tolist(), 'sample_count': len(samples),
              'max_board_translation_residual_mm': float(trans_error),
              'max_board_rotation_residual_deg': float(rot_error)}
    if offsets:
        result['tcp_offset'] = offsets[0].tolist()
    return result


def validate_held_out(samples, calibration, max_translation_mm=3.0, max_rotation_deg=1.0):
    """Check a solved transform on separately captured poses of the same fixed board."""
    import cv2
    if len(samples) < 5:
        raise ValueError('Collect at least 5 held-out views that were not used for solving')
    serial = calibration.get('camera_serial')
    if serial != samples[0].get('camera_serial') or any(sample.get('camera_serial') != serial for sample in samples):
        raise ValueError('Held-out samples and calibration must use the same camera')
    x = transform(calibration['T_flange_camera'])
    expected_offset = calibration.get('tcp_offset')
    if expected_offset is not None:
        for sample in samples:
            if 'tcp_offset' not in sample or not np.allclose(sample['tcp_offset'], expected_offset, atol=0.05):
                raise ValueError('Held-out sample TCP offset differs from calibration')
    boards = [transform(sample['T_base_flange']) @ x @ transform(sample['T_camera_board']) for sample in samples]
    center = np.mean([matrix[:3, 3] for matrix in boards], axis=0)
    trans_errors = [float(np.linalg.norm(matrix[:3, 3] - center)) for matrix in boards]
    reference_rotation = boards[0][:3, :3]
    rot_errors = [float(np.rad2deg(np.linalg.norm(cv2.Rodrigues(reference_rotation.T @ matrix[:3, :3])[0]))) for matrix in boards]
    result = dict(calibration)
    result.update({'validated': False, 'held_out_sample_count': len(samples),
                   'held_out_max_translation_residual_mm': max(trans_errors),
                   'held_out_rms_translation_residual_mm': float(np.sqrt(np.mean(np.square(trans_errors)))),
                   'held_out_max_rotation_residual_deg': max(rot_errors),
                   'held_out_consistency_passed': (max(trans_errors) <= max_translation_mm and max(rot_errors) <= max_rotation_deg),
                   'physical_point_validation_required': True})
    if not result['held_out_consistency_passed']:
        raise ValueError(f'Held-out consistency failed: {max(trans_errors):.2f} mm / {max(rot_errors):.2f} deg')
    return result


def capture(args):
    import cv2
    from .camera import GeminiCamera
    from .robot import Robot
    config = load(args.config)
    if args.cols < 3 or args.rows < 3 or not np.isfinite(args.square_mm) or args.square_mm <= 0:
        raise ValueError('Invalid board dimensions')
    with GeminiCamera(config['camera']) as camera, Robot(config['robot']) as robot:
        # Operator positions the arm in Studio, then runs this command after it stops.
        before = robot.pose()
        frame = camera.capture()
        after = robot.pose()
        if not np.allclose(before, after, atol=0.1):
            raise RuntimeError('Robot moved while sampling')
        gray = cv2.cvtColor(frame['bgr'], cv2.COLOR_BGR2GRAY)
        found, corners = cv2.findChessboardCornersSB(gray, (args.cols, args.rows))
        if not found:
            raise ValueError('Chessboard not found')
        points = np.zeros((args.rows * args.cols, 3), np.float32)
        points[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2) * args.square_mm
        fx, fy, cx, cy = frame['intrinsics']
        k = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.]])
        dist = np.asarray(frame['distortion'], float)
        ok, rvec, tvec = cv2.solvePnP(points, corners, k, dist)
        if not ok or tvec[2, 0] <= 0:
            raise ValueError('Invalid chessboard pose')
        reprojection, _ = cv2.projectPoints(points, rvec, tvec, k, dist)
        error = float(np.sqrt(np.mean(np.sum((reprojection-corners)**2, axis=2))))
        if error > 1:
            raise ValueError(f'Reprojection error {error:.2f} px exceeds 1 px')
        board = np.eye(4)
        board[:3, :3], board[:3, 3] = cv2.Rodrigues(rvec)[0], tvec.ravel()
        offset = robot.offset()
        sample = {'camera_serial': frame['serial'], 'camera_model': frame['model'],
                  'color_profile': frame['color_profile'], 'aligned_size': frame['aligned_size'],
                  'intrinsics': frame['intrinsics'], 'distortion': frame['distortion'],
                  'tcp_pose': after, 'tcp_offset': offset,
                  'T_base_flange': (pose_matrix(after) @ np.linalg.inv(pose_matrix(offset))).tolist(),
                  'T_camera_board': board.tolist(), 'reprojection_error_px': error,
                  'board': [args.cols, args.rows, args.square_mm]}
    path = Path(args.samples)
    samples = load(path) if path.exists() else []
    signature_keys = ('camera_serial', 'camera_model', 'color_profile', 'aligned_size',
                      'intrinsics', 'distortion', 'board', 'tcp_offset')
    if samples and any(samples[0].get(key) != sample.get(key) for key in signature_keys):
        raise ValueError('Camera, RGB profile, intrinsics or chessboard differs from previous samples')
    samples.append(sample)
    write(path, samples)
    cv2.drawChessboardCorners(frame['bgr'], (args.cols, args.rows), corners, True)
    cv2.imwrite(str(path.with_name(f'{path.stem}_{len(samples):03d}.jpg')), frame['bgr'])
    print(f'Saved sample {len(samples)}; reprojection {error:.3f} px')


def main():
    parser = argparse.ArgumentParser(description='Eye-in-hand calibration; never commands arm motion')
    parser.add_argument('--config', default=str(Path(__file__).resolve().parents[1] / 'config.json'))
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('capture')
    p.add_argument('--samples', default='calibration/samples.json')
    p.add_argument('--cols', type=int, required=True, help='Inner corner columns')
    p.add_argument('--rows', type=int, required=True, help='Inner corner rows')
    p.add_argument('--square-mm', type=float, required=True)
    p = sub.add_parser('solve')
    p.add_argument('--samples', default='calibration/samples.json')
    p.add_argument('--output', default='calibration/result.json')
    p = sub.add_parser('validate')
    p.add_argument('--samples', default='calibration/validation_samples.json')
    p.add_argument('--calibration', default='calibration/result.json')
    p.add_argument('--output', default='calibration/validation_result.json')
    p.add_argument('--max-translation-mm', type=float, default=3.0)
    p.add_argument('--max-rotation-deg', type=float, default=1.0)
    args = parser.parse_args()
    try:
        if args.command == 'capture':
            capture(args)
        elif args.command == 'solve':
            result = solve(load(args.samples))
            write(args.output, result)
            print(f'Saved {args.output}. Independent physical validation is still required.')
        else:
            result = validate_held_out(load(args.samples), load(args.calibration), args.max_translation_mm, args.max_rotation_deg)
            write(args.output, result)
            print(f'Saved {args.output}. Consistency passed; physical point validation is still required.')
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f'Stopped: {exc}\n')


if __name__ == '__main__':
    main()

