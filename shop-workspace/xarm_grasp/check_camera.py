"""Open Gemini 336, capture one aligned RGB-D frame, and save diagnostics."""
import argparse
import json
from pathlib import Path
import numpy as np
from .__main__ import load, write
from .camera import GeminiCamera


def main():
    parser = argparse.ArgumentParser(description="Gemini 336 RGB-D diagnostic; does not connect to robot")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config.json"))
    parser.add_argument("--output", default="runs/camera_check")
    args = parser.parse_args()
    try:
        config = load(args.config)
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        with GeminiCamera(config["camera"]) as camera:
            frame = camera.capture()
        import cv2
        if not cv2.imwrite(str(output / "color.jpg"), frame["bgr"]):
            raise IOError("Could not save color.jpg")
        np.save(output / "depth_mm.npy", frame["depth"])
        valid = frame["depth"][frame["depth"] > 0]
        metadata = {key: value for key, value in frame.items() if key not in ("bgr", "depth")}
        metadata["valid_depth_ratio"] = float(valid.size / frame["depth"].size)
        metadata["valid_depth_range_mm"] = ([float(valid.min()), float(valid.max())]
                                             if valid.size else None)
        write(output / "camera.json", metadata)
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f"Stopped: {exc}\n")


if __name__ == "__main__":
    main()
