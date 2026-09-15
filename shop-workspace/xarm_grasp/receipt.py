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
    api_key = os.environ.get(env_name, "").strip()
    if not api_key:
        raise RuntimeError(f"{env_name} is not set")
    timeout = float(receipt.get("timeout_s", 120))
    if timeout <= 0:
        raise ValueError("receipt.timeout_s must be positive")
    quality = int(receipt.get("jpeg_quality", 95))
    if not 1 <= quality <= 100:
        raise ValueError("receipt.jpeg_quality must be in 1..100")
    return receipt, api_key, timeout, quality

def recognize_receipt(bgr, config, output_dir):
    """Call GLM-OCR for one BGR image, persist evidence, and select one product."""
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
    output = Path(output_dir)
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
        (output / "receipt_result.json").write_text(
            json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    result = {
        "ocr_text": text,
        "requested_items": [selected],
        "selected_item": selected["name"],
    }
    (output / "receipt_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result



