# bio-locaith-openclaw

Thin packaging layer for using Bio-Agent OS with OpenClaw.

What it provides:

- a dedicated Python package name for OpenClaw users
- a CLI that scaffolds a local OpenClaw plugin bridge folder
- real config examples for OpenClaw and SWE-Agent
- automatic hidden sidecar startup from the OpenClaw plugin when the Bio-Agent OS API is not already running

Install locally:

```bash
pip install bio-locaith-openclaw
```

Scaffold the bridge assets:

```bash
bio-locaith-openclaw install-openclaw-plugin
```

Then use the config examples in `examples/openclaw/` and `examples/swe-agent/`.

Recommended OpenClaw config fields:

```json
{
  "apiBaseUrl": "http://127.0.0.1:8055",
  "agentName": "openclaw-brain",
  "storageDir": "~/.bio-agent-os/openclaw-brain",
  "autoStartSidecar": true,
  "sidecarLogFile": "~/.openclaw/logs/bio-locaith-sidecar.log"
}
```

With `autoStartSidecar=true`, users do not need to keep a separate terminal window open just to run the memory sidecar. The plugin will start `bio-agent-os serve-api` in the background when needed.
