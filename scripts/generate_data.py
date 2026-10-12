"""Generate sample source data for local testing and demo."""

from __future__ import annotations

import argparse
from pathlib import Path

from dataflow_ai.sample_data import generate_sample_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate DataFlow AI sample sources.")
    parser.add_argument("--output-dir", default="data/sample", help="Output directory for generated CSV and JSON files.")
    parser.add_argument("--orders", type=int, default=2400, help="Number of orders to generate.")
    parser.add_argument("--without-quality-issues", action="store_true", help="Skip inserting deliberate data quality issues.")
    args = parser.parse_args()
    paths = generate_sample_data(
        Path(args.output_dir),
        order_count=args.orders,
        include_quality_issues=not args.without_quality_issues,
    )
    for dataset, path in paths.items():
        print(f"{dataset}: {path}")


if __name__ == "__main__":
    main()

