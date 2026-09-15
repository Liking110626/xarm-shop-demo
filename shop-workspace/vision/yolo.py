"""YOLO product detection and RGB-D localization. No robot connection or motion."""
import json
from pathlib import Path
import numpy as np
from xarm_grasp.config import DEFAULT_CONFIG, load, write, resolve_entry
from xarm_grasp.coordinates import pixel_to_camera, camera_to_tcp


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
    model_path = (Path(config_path).resolve().parent / product['model']).resolve()
    if not model_path.is_file():
        raise FileNotFoundError(f'Trained YOLO weights required: {model_path}')
    from ultralytics import YOLO
    model = YOLO(str(model_path))
    if type(product['class_id']) is not int or product['class_id'] not in model.names:
        raise ValueError('class_id does not exist in model.names')
    return model


def recognize_product(frame, model, product, config, item_name=None, *,
                      include_tcp=True, tcp_offset=None):
    """Recognize one RGB-D frame and return a dict containing XYZ in mm.

    frame: GeminiCamera.capture() result (BGR, aligned depth in mm, intrinsics,
    distortion, serial). The model can be loaded once and reused.
    No capture, robot, network, file write, or motion occurs here.
    include_tcp=False needs neither hand-eye calibration nor TCP configuration.
    """
    detection = detect(model, frame, product, config["confidence"])
    point = pixel_to_camera(detection["uv"], frame["depth"], frame["intrinsics"],
                            frame.get("distortion"), **config["depth"])
    report = {"item": item_name, "class_id": product["class_id"], "detection": detection,
              "camera_point_mm": point.tolist(), "camera_serial": frame["serial"],
              "status": "vision_only"}
    if include_tcp:
        calibration = config["calibration"]
        if not calibration.get("camera_serial") or frame["serial"] != calibration["camera_serial"]:
            raise ValueError("Connected camera differs from calibrated camera")
        offset = config["robot"]["tcp_offset"] if tcp_offset is None else tcp_offset
        report["tcp_point_mm"] = camera_to_tcp(
            point, offset, calibration["T_flange_camera"]).tolist()
        report["tcp_offset_used"] = np.asarray(offset, float).tolist()
        report["calibration_validated"] = calibration.get("validated") is True
    return report


def save_detection(frame, report, output):
    """Save annotated image, aligned depth, intrinsics and the localization result."""
    import cv2
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    img = frame["bgr"].copy()
    x1, y1, x2, y2 = map(int, report["detection"]["bbox"])
    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
    if not cv2.imwrite(str(output / "detection.jpg"), img):
        raise IOError("Could not save detection image")
    if not cv2.imwrite(str(output / "color.png"), frame["bgr"]):
        raise IOError("Could not save color image")
    np.save(output / "depth_mm.npy", frame["depth"])
    distortion = frame.get("distortion")
    write(output / "frame.json", {
        "intrinsics": np.asarray(frame["intrinsics"]).tolist(),
        "distortion": None if distortion is None else np.asarray(distortion).tolist(),
        "serial": frame["serial"],
    })
    write(output / "result.json", report)


def acquire_product(camera, model, product, item_name, config, output=None,
                    receipt_report=None, *, include_tcp=True, tcp_offset=None):
    """Capture one frame; return (camera_xyz_numpy, report). Optionally save evidence."""
    frame = camera.capture()
    report = recognize_product(frame, model, product, config, item_name,
                               include_tcp=include_tcp, tcp_offset=tcp_offset)
    if receipt_report is not None:
        report["receipt"] = receipt_report
    if output is not None:
        save_detection(frame, report, output)
    return np.asarray(report["camera_point_mm"]), report


def load_saved_frame(directory):
    """Reload color.png/depth_mm.npy/frame.json saved by save_detection()."""
    import cv2
    directory = Path(directory)
    metadata = load(directory / "frame.json")
    bgr = cv2.imdecode(np.fromfile(directory / "color.png", dtype=np.uint8), cv2.IMREAD_COLOR)
    depth = np.load(directory / "depth_mm.npy", allow_pickle=False)
    if bgr is None or depth.ndim != 2 or bgr.shape[:2] != depth.shape:
        raise ValueError("Saved color and aligned depth must have the same image size")
    return {**metadata, "bgr": bgr, "depth": depth}


def locate_product(item_name, config_path=DEFAULT_CONFIG, output=None, *, include_tcp=True, frame_dir=None):
    """Open the camera, localize one product, close the camera, return the report.

    Does not connect to the robot. Uses config.robot.tcp_offset for TCP coordinates.
    Pass frame_dir to replay saved RGB-D without opening the camera.
    For repeated inference use recognize_product() with a reused camera/model.
    """
    config_path = Path(config_path).resolve()
    config = load(config_path)
    canonical_name, product = resolve_entry(config, item_name)
    model = load_model(config_path, product)
    if frame_dir is not None:
        frame = load_saved_frame(frame_dir)
        report = recognize_product(frame, model, product, config, canonical_name, include_tcp=include_tcp)
        if output is not None:
            save_detection(frame, report, output)
        return report
    from .camera import GeminiCamera
    with GeminiCamera(config["camera"]) as camera:
        _, report = acquire_product(camera, model, product, canonical_name, config,
                                    output, include_tcp=include_tcp)
    return report


def get_product_position(item_name, config_path=DEFAULT_CONFIG, *, coordinate_system="tcp", frame_dir=None):
    """One-call camera localization returning numpy [x,y,z] mm in 'tcp' or 'camera'."""
    if coordinate_system not in ("tcp", "camera"):
        raise ValueError("coordinate_system must be tcp or camera")
    report = locate_product(item_name, config_path, include_tcp=coordinate_system == "tcp", frame_dir=frame_dir)
    return np.asarray(report[f"{coordinate_system}_point_mm"], dtype=float)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Detect a product and return camera/TCP XYZ mm; no robot motion")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--item", required=True)
    parser.add_argument("--camera-only", action="store_true", help="Skip hand-eye/TCP conversion")
    parser.add_argument("--output", default="runs/yolo_test")
    parser.add_argument("--frame-dir", help="Replay saved RGB-D instead of opening the camera")
    args = parser.parse_args()
    try:
        result = locate_product(args.item, args.config, args.output, include_tcp=not args.camera_only, frame_dir=args.frame_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f"Stopped: {exc}\n")


if __name__ == "__main__":
    main()
