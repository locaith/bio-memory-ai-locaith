# Claude Code integration

The integration uses current Claude Code command hooks. Hook JSON arrives on
stdin and `bio-memory-hook` stores an immutable observation. Context-bearing
events (`SessionStart`, `UserPromptSubmit`, `UserPromptExpansion`,
`PostCompact`, `SubagentStart`) also recall relevant non-simulated,
non-rejected memories and return them through `additionalContext`.

```bash
pip install -e .
python tools/install_claude_code_hooks.py --project /path/to/repository
claude
```

Environment variables:

- `BIO_MEMORY_DB`: SQLite path. Defaults to `.bio-agent-os/memory.db`.
- `BIO_MEMORY_TENANT`: tenant identifier. Defaults to `local`.
- `BIO_MEMORY_WORKSPACE`: workspace identifier. Defaults to Claude's `cwd`.

The installer merges settings and does not overwrite unrelated hooks.
