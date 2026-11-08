import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def validate_data(data_dir: Path) -> int:
    schemas = Path(__file__).resolve().parents[1] / "catalog/schema"
    summary = json.loads((data_dir / "summary.json").read_text())
    validator = Draft202012Validator(
        json.loads((schemas / "summary.schema.json").read_text()), format_checker=FormatChecker()
    )
    validator.validate(summary)
    validator = Draft202012Validator(
        json.loads((schemas / "route-history.schema.json").read_text()),
        format_checker=FormatChecker(),
    )
    for route in summary["routes"]:
        history = json.loads((data_dir / route["history"]).read_text())
        validator.validate(history)
        assert history["route_id"] == route["id"]
    return 1 + len(summary["routes"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    args = parser.parse_args()
    count = validate_data(args.data)
    print(f"Schema validation: {count} passed, 0 failed (1 summary, {count - 1} route histories)")
