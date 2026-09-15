"""Receipt OCR and strict mapping from OCR text to configured products."""
import base64
import json
import os
import re
from pathlib import Path


DEFAULT_OCR_URL = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"


def extract_ocr_text(response):
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


def normalize_receipt_text(value):
    """Remove OCR layout noise while retaining Chinese, letters and digits."""
    return "".join(re.findall(r"[\w\u4e00-\u9fff]+", str(value).casefold(), re.UNICODE))


def match_receipt_products(text, config):
    normalized = normalize_receipt_text(text)
    if not normalized:
        raise ValueError("OCR text is empty after normalization")
    ownership = {}
    matches = []
    for display_name, product in config["products"].items():
        terms = []
        for value in (display_name, *product.get("aliases", [])):
            term = normalize_receipt_text(value)
            if not term:
                continue
            previous = ownership.setdefault(term, display_name)
            if previous != display_name:
                raise ValueError(f"Receipt alias {value!r} is shared by {previous!r} and {display_name!r}")
            if term not in terms:
                terms.append(term)
        found = sorted((term for term in terms if term in normalized), key=len, reverse=True)
        if found:
            matches.append({
                "name": display_name,
                "class_id": int(product["class_id"]),
                "matched_term": found[0],
            })
    return matches

def select_receipt_product(text, config):
    matches = match_receipt_products(text, config)
    if not matches:
        choices = "、".join(config["products"])
        raise ValueError(f"Receipt does not contain a configured product; configured products: {choices}")
    if len(matches) != 1:
        names = "、".join(item["name"] for item in matches)
        raise ValueError(f"Receipt contains multiple configured product types: {names}")
    return matches[0]


def validate_receipt_settings(config):
    receipt = config.get("receipt", {})
    env_name = receipt.get("api_key_env", "ZHIPUAI_API_KEY")
    if not isinstance(env_name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env_name):
        raise ValueError(
            'receipt.api_key_env must be an environment variable name such as '
            'ZHIPUAI_API_KEY, not the API key itself')
    api_key = os.environ.get(env_name, "").strip()
    if not api_key:
        raise RuntimeError(
            'OCR API key environment variable is not set; set the variable named '
            'by receipt.api_key_env in the terminal before running')
    timeout = float(receipt.get("timeout_s", 120))
    if timeout <= 0:
        raise ValueError("receipt.timeout_s must be positive")
    quality = int(receipt.get("jpeg_quality", 95))
    if not 1 <= quality <= 100:
        raise ValueError("receipt.jpeg_quality must be in 1..100")
    return receipt, api_key, timeout, quality

def recognize_receipt(bgr, config, output_dir=None):
    """Call GLM-OCR and return selected_item/ocr_text; optionally save evidence."""
    import cv2
    import requests

    receipt, api_key, timeout, quality = validate_receipt_settings(config)

    ok, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("Could not encode receipt image")
    image_bytes = encoded.tobytes()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise ValueError("Receipt image exceeds the OCR 10 MB limit")
    payload = {
        "model": receipt.get("model", "glm-ocr"),
        "file": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii"),
        "return_crop_images": False,
        "need_layout_visualization": False,
    }

    try:
        response = requests.post(
            receipt.get("url", DEFAULT_OCR_URL),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        raw = response.json()
    except requests.Timeout as exc:
        raise RuntimeError(f"Receipt OCR timed out after {timeout:g} seconds") from exc
    except (requests.RequestException, ValueError) as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise RuntimeError(f"Receipt OCR request failed (HTTP {status or 'N/A'})") from exc

    text = extract_ocr_text(raw)
    if not text:
        raise RuntimeError("Receipt OCR returned no md_results text")
    output = Path(output_dir) if output_dir is not None else None
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output / "receipt.jpg"), bgr):
            raise IOError("Could not save receipt image")
        (output / "receipt_ocr.md").write_text(text + "\n", encoding="utf-8")
        (output / "receipt_ocr.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    matches = match_receipt_products(text, config)
    try:
        selected = select_receipt_product(text, config)
    except ValueError as exc:
        rejected = {
            "ocr_text": text,
            "requested_items": matches,
            "status": "rejected",
            "error": str(exc),
        }
        if output is not None:
            (output / "receipt_result.json").write_text(
                json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    result = {
        "ocr_text": text,
        "requested_items": [selected],
        "selected_item": selected["name"],
    }
    if output is not None:
        (output / "receipt_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def recognize_receipt_frame(frame, config, output=None):
    """OCR a captured frame; optionally save under output/receipt/."""
    return recognize_receipt(frame['bgr'], config, None if output is None else Path(output) / 'receipt')


def recognize_receipt_file(image_path, config, output=None):
    """OCR an existing image; optionally save under output/receipt/."""
    import cv2
    import numpy as np
    path = Path(image_path).expanduser().resolve()
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f'Could not read receipt image: {path}')
    return recognize_receipt(image, config, None if output is None else Path(output) / 'receipt')


def main():
    import argparse
    from xarm_grasp.config import DEFAULT_CONFIG, load
    parser = argparse.ArgumentParser(description="Receipt OCR and product matching; no robot motion")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--image", help="Existing image; omit to capture from Gemini")
    parser.add_argument("--output", default="runs/ocr_test")
    args = parser.parse_args()
    try:
        config = load(args.config)
        if args.image:
            result = recognize_receipt_file(args.image, config, args.output)
        else:
            from .camera import GeminiCamera
            with GeminiCamera(config["camera"]) as camera:
                result = recognize_receipt_frame(camera.capture(), config, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f"Stopped: {exc}\n")


if __name__ == "__main__":
    main()
