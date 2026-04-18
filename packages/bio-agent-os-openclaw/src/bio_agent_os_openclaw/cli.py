from __future__ import annotations

import argparse

try:
    from bio_agent_os_openclaw.installer import (
        install_openclaw_plugin,
        render_openclaw_config,
        render_swe_agent_overlay,
    )
except ModuleNotFoundError:  # pragma: no cover - direct source execution helper
    from installer import (  # type: ignore
        install_openclaw_plugin,
        render_openclaw_config,
        render_swe_agent_overlay,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="bio-agent-os-openclaw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install-openclaw-plugin")
    install_parser.add_argument("--target", default=None)

    config_parser = subparsers.add_parser("print-openclaw-config")
    config_parser.add_argument("--plugin-path", default=None)

    subparsers.add_parser("print-swe-agent-config")

    args = parser.parse_args()
    if args.command == "install-openclaw-plugin":
        path = install_openclaw_plugin(target=args.target)
        print(path)
        return
    if args.command == "print-openclaw-config":
        print(render_openclaw_config(plugin_path=args.plugin_path))
        return
    if args.command == "print-swe-agent-config":
        print(render_swe_agent_overlay())
        return


if __name__ == "__main__":
    main()
