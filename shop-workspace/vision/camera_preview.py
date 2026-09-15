"""Real-time RGB or RGB-D preview for the wrist-mounted Orbbec camera."""

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from .camera import GeminiCamera


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WINDOW_NAME = "Orbbec Gemini 336 Preview"


def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def colorize_depth(depth_mm, minimum_mm=200.0, maximum_mm=2000.0):
    """Convert millimetre depth data to a viewable BGR heat map."""
    if maximum_mm <= minimum_mm:
        raise ValueError("depth-max-mm must be greater than depth-min-mm")
    depth = np.asarray(depth_mm, dtype=np.float32)
    valid = np.isfinite(depth) & (depth > 0)
    scaled = np.zeros(depth.shape, dtype=np.uint8)
    scaled[valid] = np.clip(
        (depth[valid] - minimum_mm) * 255.0 / (maximum_mm - minimum_mm),
        0,
        255,
    ).astype(np.uint8)
    # Invert so nearby objects use the warm end of the colour map.
    colored = cv2.applyColorMap(255 - scaled, cv2.COLORMAP_TURBO)
    colored[~valid] = 0
    return colored


def make_preview(frame, show_depth, depth_min_mm, depth_max_mm, fps):
    color = frame["bgr"].copy()
    cv2.putText(
        color,
        f"RGB  FPS {fps:.1f}",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    if not show_depth:
        return color

    depth = colorize_depth(frame["depth"], depth_min_mm, depth_max_mm)
    if depth.shape[:2] != color.shape[:2]:
        depth = cv2.resize(depth, (color.shape[1], color.shape[0]), interpolation=cv2.INTER_NEAREST)
    cv2.putText(
        depth,
        f"Depth {depth_min_mm:.0f}-{depth_max_mm:.0f} mm",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return np.hstack((color, depth))


def run(args):
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    frame_count = 0
    smoothed_fps = 0.0
    previous = None
    last_color = None

    if not args.no_window:
        print("相机预览已启动：Q/Esc 退出，S 保存当前彩色帧。本脚本不会连接或移动机械臂。")
    try:
        with GeminiCamera(config["camera"]) as camera:
            while True:
                frame = camera.capture()
                now = time.perf_counter()
                if previous is not None:
                    instant = 1.0 / max(now - previous, 1e-9)
                    smoothed_fps = instant if smoothed_fps == 0 else 0.9 * smoothed_fps + 0.1 * instant
                previous = now
                frame_count += 1
                last_color = frame["bgr"]

                key = -1
                if not args.no_window:
                    preview = make_preview(
                        frame,
                        args.show_depth,
                        args.depth_min_mm,
                        args.depth_max_mm,
                        smoothed_fps,
                    )
                    cv2.imshow(WINDOW_NAME, preview)
                    key = cv2.waitKey(1) & 0xFF

                if key in (27, ord("q"), ord("Q")):
                    break
                if key in (ord("s"), ord("S")):
                    output_dir = Path(args.output).resolve()
                    output_dir.mkdir(parents=True, exist_ok=True)
                    target = output_dir / f"color_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                    if not cv2.imwrite(str(target), last_color):
                        raise IOError(f"保存图片失败：{target}")
                    print(f"已保存：{target}")
                if args.max_frames and frame_count >= args.max_frames:
                    break
    finally:
        if not args.no_window:
            cv2.destroyAllWindows()
    print(f"已正常退出，共读取 {frame_count} 帧。")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"), help="项目配置文件")
    parser.add_argument("--show-depth", action="store_true", help="在彩色画面右侧同时显示深度图")
    parser.add_argument("--depth-min-mm", type=float, default=200.0, help="深度着色下限，默认 200 mm")
    parser.add_argument("--depth-max-mm", type=float, default=2000.0, help="深度着色上限，默认 2000 mm")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "runs" / "camera_preview"), help="按 S 时的图片目录")
    parser.add_argument("--no-window", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-frames", type=int, default=0, help=argparse.SUPPRESS)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        print("\n已停止。")
    except Exception as exc:
        parser.exit(1, f"已停止：{exc}\n")


if __name__ == "__main__":
    main()
