"""Capture or load a receipt image, call Zhipu GLM-OCR, and print its text."""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import cv2
import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xarm_grasp.camera import GeminiCamera
OCR_URL = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"
MAX_FILE_BYTES = 10 * 1024 * 1024


def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def capture_receipt(config, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"receipt_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    print("未指定图片：正在从 Gemini 336 拍摄一帧……")
    with GeminiCamera(config["camera"]) as camera:
        image = camera.capture()["bgr"]
    if not cv2.imwrite(str(image_path), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
        raise IOError(f"保存相机图片失败：{image_path}")
    print(f"已拍摄：{image_path.resolve()}")
    return image_path


def encode_image(image_path):
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"找不到小票图片：{image_path}")
    suffix = image_path.suffix.lower()
    mime_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    if suffix not in mime_types:
        raise ValueError("小票图片只支持 JPG、JPEG 或 PNG")
    size = image_path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"图片为 {size / 1024 / 1024:.2f} MB，超过接口 10 MB 限制")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_types[suffix]};base64,{encoded}"


def extract_text(response):
    markdown = response.get("md_results")
    if isinstance(markdown, str):
        return markdown.strip()
    if isinstance(markdown, list):
        parts = []
        for page in markdown:
            if isinstance(page, str):
                parts.append(page)
            elif isinstance(page, dict):
                value = page.get("content") or page.get("md_result") or page.get("text")
                if value:
                    parts.append(str(value))
        return "\n\n".join(parts).strip()
    return ""


def run(args):
    api_key = os.environ.get("ZHIPUAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未设置环境变量 ZHIPUAI_API_KEY；请按 README 的方式临时设置")
    config = load_config(args.config)
    output_dir = Path(args.output)
    image_path = Path(args.image).expanduser() if args.image else capture_receipt(config, output_dir)
    payload = {
        "model": "glm-ocr",
        "file": encode_image(image_path),
        "return_crop_images": False,
        "need_layout_visualization": False,
    }
    print(f"正在识别：{image_path.resolve()}")
    try:
        http_response = requests.post(
            OCR_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=args.timeout,
        )
        http_response.raise_for_status()
    except requests.Timeout as exc:
        raise RuntimeError(f"OCR 请求超过 {args.timeout:g} 秒") from exc
    except requests.RequestException as exc:
        status = getattr(exc.response, "status_code", None)
        detail = getattr(exc.response, "text", "")[:500] if exc.response is not None else str(exc)
        raise RuntimeError(f"OCR 请求失败（HTTP {status or 'N/A'}）：{detail}") from exc

    response = http_response.json()
    text = extract_text(response)
    if not text:
        raise RuntimeError("接口调用成功，但响应中没有 md_results 文本")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = image_path.stem
    text_path = output_dir / f"{stem}_ocr.md"
    json_path = output_dir / f"{stem}_ocr.json"
    text_path.write_text(text + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== OCR 识别结果 =====\n")
    print(text)
    print(f"\n文字已保存：{text_path.resolve()}")
    print(f"完整响应已保存：{json_path.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="已有的小票 JPG/PNG；省略则立即从 Gemini 336 拍一帧")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"))
    parser.add_argument("--output", default=str(PROJECT_ROOT / "runs" / "receipt_ocr"))
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        run(args)
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f"已停止：{exc}\n")


if __name__ == "__main__":
    main()
