import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const bioAgentOsOpenClaw = definePluginEntry({
  id: "bio-agent-os-openclaw",
  name: "Bio-Agent OS OpenClaw Bridge",
  description: "Bridge plugin for wiring an external Bio-Agent OS sidecar into OpenClaw.",
  kind: "memory",
  register(api) {
    api.logger.info("bio-agent-os-openclaw: bridge plugin registered");
  },
});

export { bioAgentOsOpenClaw as default };
