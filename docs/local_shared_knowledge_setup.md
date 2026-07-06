# Local MCP with Shared NAS Knowledge

This setup keeps the source code and MCP server on each member's local machine,
while sharing only the accumulated literature knowledge through a NAS folder.

## Layout

```text
Member PC
  local clone of Lipidmix_with_LLM
  local Python environment
  local MCP server started with stdio
  local or shared MS-DIAL data folder

NAS
  \\NAS\lipidmix\knowledge
  \\NAS\lipidmix\knowledge\_inbox
```

`analyses/` stays local by default. It records each member's objectives and
search logs, while promoted knowledge is shared by everyone.

## Environment Variables

PowerShell example:

```powershell
$env:LIPIDMIX_KNOWLEDGE_DIR="\\NAS\lipidmix\knowledge"
$env:LIPIDMIX_DATA_DIR="C:\path\to\msdial\output"
python server.py
```

Use a local data folder when each member analyzes different files. Use a shared
read-only data folder only when everyone should see the same MS-DIAL outputs.

Do not set `LIPIDMIX_TRANSPORT` for normal local MCP use. The default transport
is `stdio`, so the LLM client starts `python server.py` as a local subprocess.

## Claude / MCP Registration

Register the local command, not an HTTP URL:

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

## Knowledge Review Flow

1. Local MCP runs `paper_search`.
2. Relevant hits are staged into `\\NAS\lipidmix\knowledge\_inbox`.
3. A human reviews `lipidmix://knowledge/inbox`.
4. `ingest_promote` moves a note into shared `knowledge/`.
5. Every member can read it through `lipidmix://knowledge/index` and
   `lipidmix://knowledge/expand/<slug>`.

`write_note`, `ingest_promote`, and `ingest_reject` use simple lock files to
reduce accidental concurrent edits on the shared NAS folder. Still, the safest
operation rule is to avoid two people reviewing the same inbox note at once.
