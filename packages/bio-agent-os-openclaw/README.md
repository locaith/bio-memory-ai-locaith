# bio-agent-os-openclaw

Thin packaging layer for using Bio-Agent OS with OpenClaw.

What it provides:

- a dedicated Python package name for OpenClaw users
- a CLI that scaffolds a local OpenClaw plugin bridge folder
- real config examples for OpenClaw and SWE-Agent

Install locally:

```bash
pip install bio-agent-os-openclaw
```

Scaffold the bridge assets:

```bash
bio-agent-os-openclaw install-openclaw-plugin
```

Then use the config examples in `examples/openclaw/` and `examples/swe-agent/`.
