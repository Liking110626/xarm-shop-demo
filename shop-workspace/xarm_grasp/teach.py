"""Read the stopped robot pose and print an observation config snippet."""
import argparse
import json
from pathlib import Path

from .__main__ import load
from .robot import Robot, checked


def main():
    parser = argparse.ArgumentParser(description="Read-only pose helper; never enables or moves the robot")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config.json"))
    parser.add_argument("--type", choices=("joint", "tcp"), default="joint")
    args = parser.parse_args()
    try:
        config = load(args.config)
        with Robot(config["robot"]) as robot:
            before = robot.pose()
            values = checked(robot.arm.get_servo_angle(is_radian=False), "get_servo_angle") if args.type == "joint" else before
            after = robot.pose()
            if max(abs(a - b) for a, b in zip(before, after)) > 0.1:
                raise RuntimeError("Robot moved while reading pose")
        print(json.dumps({"observation": {"type": args.type, "values": [round(float(v), 4) for v in values]}}, ensure_ascii=False, indent=2))
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f"Stopped: {exc}\n")


if __name__ == "__main__":
    main()
