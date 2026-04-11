# JustThreed MCP Server

Standalone Python package that exposes Blender control tools via the
Model Context Protocol (MCP). The package is launched as a subprocess by
your AI client (Claude Desktop, Cursor, etc.) and forwards tool calls over
TCP to the JustThreed Blender extension running on `localhost:9876`.

See the [root README](../README.md) for the full project documentation.

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
