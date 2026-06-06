from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .errors import SymphonyError
from .logging_utils import configure_logging
from .service import run_service
from .workflow import global_config_dir, install_bundled_workflows, select_workflow_path, workflow_aliases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="symphony", description="Run the Symphony coding-agent orchestrator.")
    parser.add_argument(
        "workflow",
        nargs="?",
        help="Path to WORKFLOW.md or a named workflow such as 'dashboard' or 'jira-project'. Defaults to ./WORKFLOW.md.",
    )
    parser.add_argument("--port", type=int, default=None, help="Enable the optional local HTTP status server on this port.")
    parser.add_argument("--once", action="store_true", help="Run one startup cleanup + poll/reconcile pass and exit.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    parser.add_argument("--config-dir", action="store_true", help="Print the global Symphony workflow config directory and exit.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files when used with 'symphony init'.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.config_dir or args.workflow == "config-dir":
        print(global_config_dir())
        return 0
    if args.workflow == "init":
        installed = install_bundled_workflows(overwrite=args.force)
        target = global_config_dir()
        if installed:
            print(f"installed {len(installed)} workflow(s) to {target}")
            for path in installed:
                print(path)
        else:
            print(f"no workflow files changed in {target}")
        return 0
    if args.workflow in {"workflows", "list-workflows"}:
        print("available named workflows:")
        for alias, filename in sorted(workflow_aliases().items()):
            print(f"  {alias}: {filename}")
        return 0

    configure_logging(args.log_level)
    workflow_path = select_workflow_path(args.workflow)
    if not workflow_path.exists():
        print(f"workflow file not found: {workflow_path}", file=sys.stderr)
        return 2
    try:
        asyncio.run(run_service(workflow_path, cli_port=args.port, once=args.once))
    except KeyboardInterrupt:
        return 0
    except SymphonyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"unexpected error: {exc}", file=sys.stderr)
        return 1
    return 0
