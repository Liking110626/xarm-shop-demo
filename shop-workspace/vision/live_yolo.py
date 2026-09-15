"""Show Gemini 336 video and draw only the highest-confidence product box."""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xarm_grasp.camera import GeminiCamera


def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def product_catalog(config):
    """Return configured class IDs, display names and accepted item aliases."""
    by_id = {}
    aliases = {}
    for display_name, product in config["products"].items():
        class_id = int(product["class_id"])
        by_id[class_id] = display_name
        for name in (display_name, *product.get("aliases", [])):
            aliases[name.casefold()] = class_id
    return by_id, aliases


def select_best(boxes, allowed_ids):
    """Select (xyxy, confidence, class_id) for the strongest allowed box."""
    best = None
    for box in boxes:
        class_id = int(box.cls.item())
        if class_id not in allowed_ids:
            continue
        candidate = (box.xyxy[0].cpu().tolist(), float(box.conf.item()), class_id)
        if best is None or candidate[1] > best[1]:
            best = candidate
    return best


def ascii_label(display_name, class_id, confidence):
    common = {
        "可口可乐罐装": "Coke",
        "雪碧罐装": "Sprite",
        "芬达罐装": "Fanta",
        "水溶C100瓶装": "C100",
        "名仁苏打水饮料": "Mingren soda",
        "东方树叶茉莉花茶": "Oriental Leaf jasmine",
    }
    return f"{common.get(display_name, f'class {class_id}')} {confidence:.2f}"


def run(args):
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    names_by_id, aliases = product_catalog(config)
    allowed_ids = set(names_by_id)
    if args.item:
        class_id = aliases.get(args.item.casefold())
        if class_id is None:
            choices = "、".join(config["products"])
            raise ValueError(f"未知商品 {args.item!r}；可选：{choices}")
        allowed_ids = {class_id}

    model_paths = {
        (config_path.parent / product["model"]).resolve()
        for product in config["products"].values()
        if int(product["class_id"]) in allowed_ids
    }
    if len(model_paths) != 1:
        raise ValueError("所选商品必须使用同一个 YOLO 模型")
    model_path = model_paths.pop()
    if not model_path.is_file():
        raise FileNotFoundError(f"找不到 YOLO 权重：{model_path}")
    model = YOLO(str(model_path))
    invalid = sorted(allowed_ids - set(model.names))
    if invalid:
        raise ValueError(f"模型中不存在配置的类别：{invalid}")

    threshold = args.conf if args.conf is not None else float(config["confidence"])
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_count = 0
    previous = time.perf_counter()
    fps = 0.0
    print("按 Q 或 Esc 退出；按 S 保存当前带框画面。此脚本不会连接或移动机械臂。")
    if args.item:
        print(f"只识别：{names_by_id[next(iter(allowed_ids))]}")
    else:
        print("识别范围：config.json 中已配置的商品；每帧只显示置信度最高的一个。")

    try:
        with GeminiCamera(config["camera"]) as camera:
            while True:
                frame = camera.capture()["bgr"]
                result = model.predict(
                    frame, conf=threshold, imgsz=args.imgsz,
                    device=args.device, verbose=False
                )[0]
                best = select_best(result.boxes, allowed_ids)
                annotated = frame.copy()
                if best is not None:
                    xyxy, confidence, class_id = best
                    x1, y1, x2, y2 = (int(round(value)) for value in xyxy)
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    label = ascii_label(names_by_id[class_id], class_id, confidence)
                    cv2.putText(annotated, label, (x1, max(24, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2,
                                cv2.LINE_AA)
                else:
                    cv2.putText(annotated, "No configured product detected", (15, 32),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 180, 255), 2,
                                cv2.LINE_AA)

                now = time.perf_counter()
                instant_fps = 1.0 / max(now - previous, 1e-9)
                fps = instant_fps if frame_count == 0 else fps * 0.9 + instant_fps * 0.1
                previous = now
                cv2.putText(annotated, f"FPS {fps:.1f}", (15, annotated.shape[0] - 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2,
                            cv2.LINE_AA)
                frame_count += 1

                key = -1
                if not args.no_window:
                    cv2.imshow("Gemini 336 - highest confidence product", annotated)
                    key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q"), ord("Q")):
                    break
                if key in (ord("s"), ord("S")):
                    target = output_dir / f"detection_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                    if not cv2.imwrite(str(target), annotated):
                        raise IOError(f"保存图片失败：{target}")
                    print(f"已保存：{target.resolve()}")
                if args.max_frames and frame_count >= args.max_frames:
                    break
    finally:
        cv2.destroyAllWindows()
    print(f"已正常退出，共处理 {frame_count} 帧。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"))
    parser.add_argument("--item", help="只框指定商品，例如：可乐；省略则比较全部已配置商品")
    parser.add_argument("--conf", type=float, help="置信度阈值，默认读取 config.json")
    parser.add_argument("--imgsz", type=int, default=640, help="YOLO 推理尺寸")
    parser.add_argument("--device", default="cpu", help="Ultralytics 设备，例如 cpu、0")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "runs" / "live_yolo"))
    parser.add_argument("--no-window", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-frames", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        run(args)
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f"已停止：{exc}\n")


if __name__ == "__main__":
    main()
