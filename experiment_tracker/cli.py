"""Command-line interface for the local experiment tracker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .store import ExperimentError, ExperimentStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="experiment-tracker",
        description="Track local machine-learning runs in SQLite.",
    )
    parser.add_argument("database", type=Path, help="SQLite database path")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create a run")
    create.add_argument("--project", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--notes", default="")

    metric = commands.add_parser("log-metric", help="log or update a metric")
    metric.add_argument("--run-id", required=True, type=int)
    metric.add_argument("--name", required=True)
    metric.add_argument("--value", required=True, type=float)
    metric.add_argument("--step", default=0, type=int)

    parameter = commands.add_parser("log-param", help="log or update a JSON parameter")
    parameter.add_argument("--run-id", required=True, type=int)
    parameter.add_argument("--key", required=True)
    parameter.add_argument("--value", required=True)

    artifact = commands.add_parser("add-artifact", help="record an artifact hash")
    artifact.add_argument("--run-id", required=True, type=int)
    artifact.add_argument("path", type=Path)
    artifact.add_argument("--name")

    finish = commands.add_parser("finish", help="finish a run")
    finish.add_argument("--run-id", required=True, type=int)
    finish.add_argument("--status", choices=("completed", "failed", "cancelled"), default="completed")

    list_runs = commands.add_parser("list", help="list runs")
    list_runs.add_argument("--project")
    list_runs.add_argument("--limit", type=int, default=20)

    best = commands.add_parser("best", help="find the best completed run")
    best.add_argument("--project", required=True)
    best.add_argument("--metric", required=True)
    best.add_argument("--minimize", action="store_true")

    show = commands.add_parser("show", help="show a run as JSON")
    show.add_argument("--run-id", required=True, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with ExperimentStore(args.database) as store:
            if args.command == "create":
                run = store.create_run(args.project, args.name, notes=args.notes)
                print(f"Created run #{run.id}: {run.project}/{run.name}")
            elif args.command == "log-metric":
                metric = store.log_metric(args.run_id, args.name, args.value, step=args.step)
                print(f"Logged {metric.name}={metric.value:g} at step {metric.step} for run #{args.run_id}")
            elif args.command == "log-param":
                try:
                    value = json.loads(args.value)
                except json.JSONDecodeError:
                    value = args.value
                store.log_param(args.run_id, args.key, value)
                print(f"Logged parameter {args.key} for run #{args.run_id}")
            elif args.command == "add-artifact":
                artifact = store.add_artifact(args.run_id, args.path, name=args.name)
                print(f"Recorded {artifact.name}: sha256={artifact.sha256}")
            elif args.command == "finish":
                run = store.finish_run(args.run_id, status=args.status)
                print(f"Run #{run.id} marked {run.status}.")
            elif args.command == "list":
                for run in store.list_runs(project=args.project, limit=args.limit):
                    print(f"#{run.id:<4} {run.status:<9} {run.project}/{run.name}  {run.started_at}")
            elif args.command == "best":
                result = store.best_run(args.project, args.metric, minimize=args.minimize)
                if result is None:
                    print("No completed run has that metric.")
                    return 1
                run, value = result
                print(f"#{run.id} {run.project}/{run.name}: {args.metric}={value:g}")
            elif args.command == "show":
                print(json.dumps(store.summary(args.run_id), indent=2, sort_keys=True))
        return 0
    except ExperimentError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
