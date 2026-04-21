from __future__ import annotations

import json
import shutil
from importlib import resources
from pathlib import Path


PRIMARY_PLUGIN_ID = "bio-locaith-openclaw"
LEGACY_PLUGIN_ID = "bio-agent-os-openclaw"
PLUGIN_DIR_NAME = PRIMARY_PLUGIN_ID


def default_openclaw_target() -> Path:
    return Path.home() / ".openclaw" / "extensions" / PLUGIN_DIR_NAME


def install_openclaw_plugin(target: str | None = None) -> Path:
    destination = Path(target) if target else default_openclaw_target()
    destination.mkdir(parents=True, exist_ok=True)
    source_assets = Path(__file__).resolve().parent / "assets"
    if source_assets.exists():
        assets = source_assets
        asset_iter = list(assets.iterdir())
    else:
        try:
            assets = resources.files("bio_agent_os_openclaw").joinpath("assets")
            asset_iter = list(assets.iterdir())
        except ModuleNotFoundError:  # pragma: no cover - direct source execution helper
            assets = source_assets
            asset_iter = list(assets.iterdir())
    for asset in asset_iter:
        shutil.copyfile(asset, destination / asset.name)
    return destination


def render_openclaw_config(plugin_path: str | None = None) -> str:
    resolved = plugin_path or str(default_openclaw_target())
    payload = {
        "plugins": {
            "enabled": True,
            "load": {"paths": [resolved]},
            "slots": {"memory": PRIMARY_PLUGIN_ID},
            "entries": {
                PRIMARY_PLUGIN_ID: {
                    "enabled": True,
                    "config": {
                        "apiBaseUrl": "http://127.0.0.1:8055",
                        "agentName": "openclaw-brain",
                        "storageDir": "~/.bio-agent-os/openclaw-brain",
                        "workspaceId": "main",
                        "projectVersion": "v1",
                        "autoStartSidecar": True,
                        "sidecarLogFile": "~/.openclaw/logs/bio-locaith-sidecar.log",
                    },
                }
            },
        }
    }
    return json.dumps(payload, indent=2)


def render_swe_agent_overlay() -> str:
    return """agent:
  templates:
    instance_template: |-
      <uploaded_files>
      {{working_dir}}
      </uploaded_files>
      I've uploaded a python code repository in the directory {{working_dir}}. Consider the following PR description:
      <pr_description>
      {{problem_statement}}
      </pr_description>
      Bio-Agent OS sidecar is available at ${BIO_AGENT_API_BASE_URL}. Reuse long-term project memory before making risky changes.
  tools:
    env_variables:
      BIO_AGENT_API_BASE_URL: http://127.0.0.1:8055
      BIO_AGENT_AGENT_NAME: swe-agent-brain
      BIO_AGENT_WORKSPACE_ID: repo-main
"""
