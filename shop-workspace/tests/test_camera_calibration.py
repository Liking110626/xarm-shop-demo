"""Include the calibration suite when running unittest discover -s tests."""
from pathlib import Path


def load_tests(loader, tests, pattern):
    workspace = Path(__file__).resolve().parents[1]
    tests.addTests(loader.discover(
        str(workspace / "camera_calibration" / "tests"),
        pattern=pattern or "test*.py", top_level_dir=str(workspace)))
    return tests
