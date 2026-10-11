"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from clearclassnet.config import config_dir, load_config, set_config
from clearclassnet.dispatch.registry import get_registry
from clearclassnet.log import setup_logging
from clearclassnet.plugin import load_plugins
from clearclassnet.proxy.engine import ProxyEngine
from clearclassnet.tls.ca import CertificateAuthority
from clearclassnet.tls.store import install_trust, linux_followup, trust_command


def main(argv: list[str] | None = None) -> None:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument(
        "-c",
        "--config",
        default=argparse.SUPPRESS,
        help="path to a TOML config file",
    )
    parser = argparse.ArgumentParser(prog="clearclassnet", parents=[config_parser])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "run",
        help="listen for local proxy traffic",
        parents=[config_parser],
        conflict_handler="resolve",
    )
    sub.add_parser(
        "check",
        help="load plugins and print registered matchers",
        parents=[config_parser],
        conflict_handler="resolve",
    )

    ca = sub.add_parser("ca", help="manage the local certificate authority")
    ca_sub = ca.add_subparsers(dest="ca_command", required=True)
    export_parser = ca_sub.add_parser("export", help="copy the CA certificate")
    export_parser.add_argument("--out", type=Path, required=True)
    ca_sub.add_parser("path", help="print the CA certificate path")
    ca_sub.add_parser("install", help="trust the CA certificate for the current user")

    args = parser.parse_args(argv)
    config = getattr(args, "config", None)
    if args.command == "run":
        _run(config)
    elif args.command == "check":
        _check(config)
    elif args.command == "ca":
        _ca(args.ca_command, getattr(args, "out", None))


def format_matchers() -> str:
    """Render the registry for `clearclassnet check`."""
    lines = [
        f"[{matcher.priority}] {matcher.phase} block={matcher.block} "
        f"{matcher.rule} {matcher.qualname}"
        for matcher in get_registry().all()
    ]
    if not lines:
        return "No matchers registered."
    return "\n".join(lines)


def _run(config_path: str | None) -> None:
    config = _load(config_path)
    setup_logging(config.logging.level)
    set_config(config)
    load_plugins(config)
    engine = ProxyEngine(config, CertificateAuthority(config_dir() / "ca"))
    try:
        asyncio.run(engine.serve())
    except KeyboardInterrupt:
        return


def _check(config_path: str | None) -> None:
    config = _load(config_path)
    set_config(config)
    load_plugins(config)
    print(format_matchers())


def _ca(command: str, out: Path | None) -> None:
    authority = CertificateAuthority(config_dir() / "ca")
    if command == "path":
        print(authority.cert_path)
        return
    if command == "export":
        if out is None:
            raise SystemExit("export requires --out")
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(authority.cert_path, out)
        print(out)
        return
    if command == "install":
        command_line = " ".join(trust_command(authority.cert_path))
        print(f"running: {command_line}")
        completed = install_trust(authority.cert_path)
        if completed.returncode != 0:
            print(completed.stdout, file=sys.stdout, end="")
            print(completed.stderr, file=sys.stderr, end="")
            followup = linux_followup()
            if followup:
                print(f"then run: {followup}")
            raise SystemExit(completed.returncode)
        followup = linux_followup()
        if followup:
            print(f"then run: {followup}")
        print(f"trusted {authority.cert_path}")


def _load(config_path: str | None):
    if config_path:
        return load_config(Path(config_path))
    return load_config()
