"""The common CLI used by all independently installed skill entrypoints."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys

from .common import EvaluationError, TASKS, read_json


def parser():
    root = argparse.ArgumentParser(prog="llm-eval", description="Six serial, resumable API benchmarks")
    commands = root.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Explicitly acquire or import data")
    prepare.add_argument("--test", choices=TASKS, required=True)
    prepare.add_argument("--data-dir", type=Path, required=True)
    source = prepare.add_mutually_exclusive_group(required=True)
    source.add_argument("--download", action="store_true")
    source.add_argument("--local", type=Path)
    prepare.add_argument("--source", help="Local data provenance name")
    prepare.add_argument("--revision", required=True, help="Explicit dataset revision or local provenance revision")
    prepare.add_argument("--split", help="Required when importing local data")
    run = commands.add_parser("run", help="Generate once per item and score serially")
    run.add_argument("--test", choices=TASKS, required=True)
    run.add_argument("--data-dir", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--base-url", required=True, help="OpenAI API base URL, for example http://localhost:8000/v1")
    run.add_argument("--max-tokens", type=int)
    run.add_argument("--temperature", type=float, default=0)
    run.add_argument("--top-p", type=float, default=1)
    run.add_argument("--limit", type=int)
    run.add_argument("--extra-body", type=Path, help="Explicit JSON model-specific generation options")
    run.add_argument("--thinking", action="store_true", help="Require separated reasoning or a closing think tag")
    run.add_argument("--api-timeout", type=float, default=600)
    run.add_argument("--score-timeout", type=float)
    run.add_argument("--code-image", help="Immutable local Docker sha256 image ID")
    for name in ("resume", "score"):
        command = commands.add_parser(name, help="Continue saved run" if name == "resume" else "Offline scoring only")
        command.add_argument("--output-dir", type=Path, required=True)
        command.add_argument("--data-dir", type=Path, help="Relocated identical prepared data")
        command.add_argument("--test", choices=TASKS, help="Optional assertion of saved benchmark identity")
    report = commands.add_parser("report", help="Rebuild JSON, CSV and Markdown reports offline")
    report.add_argument("--output-dir", type=Path, required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare":
            from .data import prepare_local, prepare_remote
            if args.local:
                if not args.source or not args.split:
                    raise EvaluationError("Local prepare requires --source and --split")
                result = prepare_local(args.test, args.local, args.data_dir, args.source, args.revision, args.split)
            else:
                result = prepare_remote(args.test, args.data_dir, args.revision)
            print(json.dumps({"task": result["task"], "count": result["count"], "items_sha256": result["items_sha256"]}))
        elif args.command == "report":
            from .report import build_report
            result = build_report(args.output_dir)
            print(json.dumps(result, ensure_ascii=False))
        else:
            from .runner import evaluate, generation_config
            config = None
            if args.command == "run":
                extra = read_json(args.extra_body) if args.extra_body else None
                config = generation_config(args.test, args.model, args.base_url, args.max_tokens,
                                           args.temperature, args.top_p, extra, args.thinking,
                                           args.api_timeout, args.score_timeout, args.code_image, args.limit)
            elif args.test:
                saved = read_json(args.output_dir / "config.json")
                if args.test != saved["config"]["task"]:
                    raise EvaluationError("Requested benchmark differs from saved run")
            result = evaluate(args.data_dir, args.output_dir, config, args.command)
            print(json.dumps(result, ensure_ascii=False))
    except (EvaluationError, OSError, ValueError, KeyError, ImportError, importlib.metadata.PackageNotFoundError) as error:
        # Avoid echoing paths, raw API bodies or third-party exception contents containing credentials.
        message = str(error) if isinstance(error, EvaluationError) else "Dependency, data or filesystem operation failed"
        print(f"llm-eval stopped. {message}", file=sys.stderr)
        return 2
    return 0
