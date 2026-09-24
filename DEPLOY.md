# Local MCP + NAS Knowledge Deployment

The recommended team setup is:

- Source code: each member keeps a local clone.
- MCP server: each member runs the local `server.py` through stdio.
- Literature search: unchanged; each local MCP server can call Europe PMC.
- Knowledge: shared on the NAS through `LIPIDMIX_KNOWLEDGE_DIR`.
- Analyses/objectives: local by default, unless a specific project needs shared
  objective records.

```text
[member PC] local python server.py --stdio
      |
      | reads/writes shared notes
      v
[NAS] \\NAS\lipidmix\knowledge
      ├─ *.md
      └─ _inbox\*.md
```

## NAS Preparation

Create one shared folder for knowledge:

```powershell
mkdir \\NAS\lipidmix\knowledge
mkdir \\NAS\lipidmix\knowledge\_inbox
```

Seed it once from an existing checkout if needed:

```powershell
Copy-Item .\knowledge\* \\NAS\lipidmix\knowledge -Recurse -Force
```

## Member Setup

Install dependencies locally:

```powershell
python -m pip install -r requirements.txt
```

Set environment variables in the MCP client configuration, or in a shell when
testing manually:

```powershell
$env:LIPIDMIX_KNOWLEDGE_DIR="\\NAS\lipidmix\knowledge"
$env:LIPIDMIX_DATA_DIR="C:\path\to\msdial\output"
python server.py
```

Do not set `LIPIDMIX_TRANSPORT=streamable-http` for this mode. The default
transport is `stdio`.

## MCP Client Example

```json
{
  "mcpServers": {
    "ms-data-parser": {
      "command": "python",
      "args": ["C:\\Users\\<name>\\Lipidmix_with_LLM\\server.py"],
      "env": {
        "LIPIDMIX_KNOWLEDGE_DIR": "\\\\NAS\\lipidmix\\knowledge",
        "LIPIDMIX_DATA_DIR": "C:\\path\\to\\msdial\\output"
      }
    }
  }
}
```

## Receiving Updates

Each member's clone is checked against `origin/main` once per server start. The
check runs in a background thread, so it never delays a tool call, and it only
fetches — **the server never pulls on its own**. A running MCP server keeps its
already-imported modules in memory, so swapping code underneath a live process
leaves it half-old and half-new; applying an update is therefore an explicit
user action.

When the clone is behind, the notice appears in `load_dataset` (as a line at the
top of its output) and in the `dataset_status` / `console_status` payloads (as an
`update_available` key). Up to date, nothing is added.

To apply an update, ask Claude to run the `server_update` tool (say "update the
server"). It fast-forwards the clone to `origin/main` and, only when
`requirements.txt` changed in that range, reinstalls dependencies with the same
Python that runs the server.

`server_update` **does not touch any process**. Restarting is the member's job:

> Apply the update, then **restart Claude Desktop**. `git pull` alone leaves the
> old server process running, which is why the notice says so.

There is no `/update-ms-data-parser` slash command on purpose. Claude Desktop
cannot currently attach prompts from *local stdio* MCP servers — tools work,
prompts fail with "Failed to attach prompt" (anthropics/claude-code#82045, open
since 2026-07-28). The tool name is written into the update notice instead, so
the notice itself is the entry point.

`server_update` refuses, without changing anything, when:

| `reason` | Meaning |
|---|---|
| `dirty_worktree` | Uncommitted changes would be swept into the pull. Commit or discard them first. |
| `not_on_main` | The clone is on a feature branch — a developer's tree, not a distribution. |
| `not_fast_forward` | The clone has its own commits. No merge or rebase is attempted. |
| `fetch_failed` | Offline or no credentials. Stale refs are never reported as "up to date". |
| `git_unavailable` | No `git`, or the server was unpacked rather than cloned. |

`status="updated_with_warning"` means the code moved but `pip install` failed:
run `pip install -r requirements.txt` by hand **before** restarting, or the
server may not start.

The equivalent by hand stays available:

```powershell
git pull
python -m pip install -r requirements.txt
```

The check stays silent whenever it cannot be sure: no network, no credentials,
no `git` on PATH, or a checkout that is not on `main`. Silence means "no news",
not "up to date".

## Operational Notes

- `knowledge/` is the only shared mutable state in the standard setup.
- `analyses/` remains local, so each member's objective/search log does not
  collide with others.
- `knowledge/_inbox` is shared. Review ownership should be clear when multiple
  people are promoting or rejecting notes.
- Note writes, promote, and reject operations use simple lock files
  (`.lipidmix.lock`) to reduce concurrent write corruption on the NAS.
- `pai2_parser` returns the PCA plot through MCP only. It no longer writes
  `pca_plot_latest.png` into the data directory.
