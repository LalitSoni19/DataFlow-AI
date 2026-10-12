"""Run the data pipeline locally."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the DataFlow AI pipeline.")
    parser.add_argument("--source-dir", help="Path to folder containing source data files.")
    parser.add_argument("--regenerate-sample", action="store_true", help="Regenerate sample dataset before running.")
    args = parser.parse_args()
    settings = Settings.from_environment(Path.cwd())
    runner = PipelineRunner(settings)
    summary = (
        runner.run_from_sources(Path(args.source_dir))
        if args.source_dir
        else runner.run_sample_pipeline(regenerate_sample=args.regenerate_sample)
    )
    print(json.dumps(summary.to_dict(), indent=2))


if __name__ == "__main__":
    main()

