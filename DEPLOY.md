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
