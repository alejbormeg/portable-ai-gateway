# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from portable_ai_gateway.cli import execute_cli
from portable_ai_gateway.config import ConfigurationError, Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portable-ai-gateway")
    subcommands = parser.add_subparsers(dest="mode", required=True)

    for name, default_port in (("data-plane", 8080), ("control-plane", 8081)):
        server = subcommands.add_parser(name)
        # Container ingress is configured by the deployment profile.
        server.add_argument("--host", default=os.getenv("PAG_HOST", "0.0.0.0"))  # nosec B104
        server.add_argument("--port", type=int, default=int(os.getenv("PORT", str(default_port))))
        server.add_argument("--workers", type=int, default=int(os.getenv("PAG_WORKERS", "1")))

    cli = subcommands.add_parser("cli")
    groups = cli.add_subparsers(dest="cli_group", required=True)

    bootstrap = groups.add_parser("bootstrap")
    bootstrap.add_argument("--tenant-id", default="default")
    bootstrap.add_argument("--tenant-name", default="Default tenant")
    bootstrap.add_argument("--sensitive", action="store_true")
    bootstrap.add_argument("--no-api-key", action="store_true")
    bootstrap.add_argument("--api-key-expiry-days", type=int, default=30)

    config = groups.add_parser("config")
    config_actions = config.add_subparsers(dest="config_action", required=True)
    validate = config_actions.add_parser("validate")
    validate.add_argument("--file", required=True)
    apply = config_actions.add_parser("apply")
    apply.add_argument("--file", required=True)
    export = config_actions.add_parser("export")
    export.add_argument("--output", default="-")
    export.add_argument("--version")

    groups.add_parser("doctor")
    provider = groups.add_parser("provider")
    provider_actions = provider.add_subparsers(dest="provider_action", required=True)
    check = provider_actions.add_parser("check")
    check.add_argument("--provider-id", required=True)
    check.add_argument("--upstream-model")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        if args.mode == "data-plane":
            uvicorn.run(
                "portable_ai_gateway.apps:create_data_plane",
                factory=True,
                host=args.host,
                port=args.port,
                workers=args.workers,
                log_level=settings.log_level.lower(),
                access_log=False,
            )
            return 0
        if args.mode == "control-plane":
            uvicorn.run(
                "portable_ai_gateway.apps:create_control_plane",
                factory=True,
                host=args.host,
                port=args.port,
                workers=args.workers,
                log_level=settings.log_level.lower(),
                access_log=False,
            )
            return 0
        return execute_cli(args, settings)
    except (ConfigurationError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
