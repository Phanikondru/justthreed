# JustThreed — control Blender with natural language

[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/Phanikondru/justthreed/blob/main/LICENSE)
[![Sponsor](https://img.shields.io/github/sponsors/Phanikondru?label=Sponsor&logo=GitHub&color=ea4aaa)](https://github.com/sponsors/Phanikondru)

JustThreed is a Python MCP server that exposes Blender control tools to any
Model Context Protocol (MCP) compatible AI client — Claude Desktop, Claude
Code, Gemini CLI, Cursor, VSCode, Ollama, and more. Describe what you want
in plain English and watch Blender build it:

```
"Create a low poly cabin in the woods with warm lighting"
"Add a stone texture to the ground plane"
"Render the scene from a top-down angle"
```

This package is the AI-client-side half of JustThreed. The other half is a
Blender addon that opens a TCP socket on `localhost:9876` and executes the
tool calls against the `bpy` API. **You need both pieces installed for
anything to work** — see the full setup guide in the repository.

- **Repository & full docs:** <https://github.com/Phanikondru/justthreed>
- **Issues:** <https://github.com/Phanikondru/justthreed/issues>
- **Sponsor:** <https://github.com/sponsors/Phanikondru>

## Quick install

Assuming the Blender addon is already installed and its MCP socket server is
running on port 9876, add JustThreed to your AI client's MCP config:

```json
{
  "mcpServers": {
    "justthreed": {
      "command": "uvx",
      "args": ["justthreed"]
    }
  }
}
```

Or, for Claude Code:

```bash
claude mcp add --scope user justthreed -- uvx justthreed
```

For per-client setup (Claude Desktop, Gemini CLI, Cursor, Ollama,
OpenRouter) and the Blender addon install steps, see the
[main README on GitHub](https://github.com/Phanikondru/justthreed#local-setup).

## How it works

```
AI Client (Claude / Gemini / Cursor / Ollama / ...)
       ↓
JustThreed MCP Server (this package)  — translates intent to commands
       ↓
JustThreed Blender addon — socket server on port 9876
       ↓
Blender (bpy API) — executes in real time
```

## Development

Run against the MCP Inspector (requires the Blender extension running with
the MCP Server started):

```bash
uv run --with 'mcp[cli]' mcp dev src/justthreed/server.py
```

Run the server directly (stdio transport — what Claude Desktop does):

```bash
uv run justthreed
```

## License

MIT — see [LICENSE](https://github.com/Phanikondru/justthreed/blob/main/LICENSE).
