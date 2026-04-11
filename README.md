# 🎨 JustThreed

> Control Blender with natural language — works with Claude, Gemini, ChatGPT, Cursor, VSCode, and local models via Ollama. One addon. Zero config. Fully free and open source.

![License](https://img.shields.io/badge/license-MIT-green)
![Blender](https://img.shields.io/badge/Blender-4.5%2B-orange)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Stars](https://img.shields.io/github/stars/yourusername/justthreed?style=social)

---

## ✨ What is JustThreed?

JustThreed connects Blender to any AI model through the Model Context Protocol (MCP). Just describe what you want in plain English and watch it appear in Blender — no scripting, no manual clicking, no prior Blender experience needed.

```
"Create a low poly cabin in the woods with warm lighting"
"Add a stone texture to the ground plane"
"Render the scene from a top-down angle"
"Create 10 random trees scattered around the scene"
```

---

## 🚀 What Makes JustThreed Different

| Feature | JustThreed | Existing tools |
|---|---|---|
| Setup | Single addon, zero config | Multiple steps, config files |
| AI models | Claude, Gemini, GPT, Ollama, Cursor | Mostly Claude-only |
| Chat panel | Built into Blender sidebar | Separate app required |
| Project memory | Remembers your assets & style | Session only |
| Cost | 100% free | Some paid tiers |
| License | MIT open source | Mixed |

---

## 🤖 Supported AI Models

Works with any MCP-compatible AI client:

- **Claude Desktop** (Anthropic)
- **Cursor** / **VSCode** with MCP extension
- **Gemini CLI** (Google)
- **ChatGPT** with MCP support
- **Ollama** — run Llama 3, Mistral, Qwen, DeepSeek locally for **100% free, offline use**
- **OpenRouter** — access free models like Gemini Flash, DeepSeek R1

> 💡 **Want zero cost forever?** Use Ollama with a local model. No API key, no internet, no limits.

---

## ⚡ Features

- **Natural language 3D modeling** — create, modify, and delete objects by describing them
- **Material & texture control** — apply colors, materials, and textures via prompts
- **Scene management** — control lighting, cameras, and environment
- **Asset library integration** — pull assets from Poly Haven directly
- **Python code execution** — run arbitrary Blender Python scripts via AI
- **Two-way communication** — AI can read your current scene before making changes
- **Built-in chat panel** — prompt directly from Blender's sidebar (no app switching)
- **Project memory** — remembers your asset preferences and scene style
- **Batch operations** — "create 20 random rock variations with different materials"

---

## 📋 Requirements

- Blender 4.5.0 or newer (tested on 4.5.0 — should work on any later version)
- Python 3.11 or newer (bundled with Blender 4.5+)
- `uv` package manager
- One of the supported AI clients listed above

---

## 🛠️ Installation

### Step 1 — Install uv

**Windows:**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2 — Install the Blender Addon

1. Download `addon.py` from this repository
2. Open Blender → **Edit** → **Preferences** → **Add-ons**
3. Click **Install from Disk** and select `addon.py`
4. Enable the addon by ticking the checkbox next to **Interface: JustThreed**

### Step 3 — Start the MCP Server in Blender

1. Press **N** in the 3D viewport to open the side panel
2. Find the **JustThreed** tab
3. Click **Start MCP Server**
4. You should see: `JustThreed MCP Server started on port 9876`

### Step 4 — Connect Your AI Client

Pick your preferred AI client below:

<details>
<summary><b>Claude Desktop</b></summary>

Go to **Claude → Settings → Developer → Edit Config** and add:

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

Restart Claude Desktop. You will see a hammer icon confirming the connection.
</details>

<details>
<summary><b>Cursor / VSCode</b></summary>

Open your MCP settings file and add:

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
</details>

<details>
<summary><b>Gemini CLI</b></summary>

1. Install Gemini CLI:
   ```bash
   npm install -g @google/gemini-cli
   ```
2. Edit `~/.gemini/settings.json` and add the `mcpServers` block (merge it with whatever is already in the file — don't overwrite the existing keys):
   ```json
   {
     "mcpServers": {
       "justthreed": {
         "command": "uvx",
         "args": ["justthreed"],
         "timeout": 60000
       }
     }
   }
   ```
3. Launch `gemini` in a fresh terminal.
4. Inside the CLI, run `/mcp` — you should see `justthreed` listed as `CONNECTED` with all of JustThreed's tools.

> ⚠️ **Important — Gemini chains tool calls differently than Claude. Read this before your first prompt.**
>
> If you paste a multi-step prompt like *"create a cube, add a material, add a light, add a camera, and render"*, **Gemini will run the first tool and then stop**, waiting for you to confirm before calling the next one. This is not a bug and not a JustThreed problem — it is how Gemini CLI's tool-use policy works by default. Claude Desktop, by contrast, chains the whole plan in a single turn, which is why the same prompt feels "instant" in Claude and "stuck" in Gemini.
>
> You have three ways to fix it — pick whichever fits your workflow:
>
> **1. Tell Gemini to keep going.** Reply with a single sentence like:
> ```
> Proceed with the remaining steps without pausing for confirmation.
> If any step fails, report the error and continue with the next one.
> ```
>
> **2. Turn on YOLO mode (auto-approve every tool call).** Inside the CLI type `/yolo`, or launch with `gemini --yolo`. Safe for JustThreed because every operation is undoable in Blender with `Ctrl+Z` and the MCP server is local-only — just remember to turn it off when you're using other MCP servers that touch the internet.
>
> **3. Send smaller batched prompts.** Instead of one 13-step mega-prompt, break it into 3–4 checkpoints (shape → materials → lighting → render). This plays to Gemini's natural one-step-at-a-time rhythm and gives you an inspection point after each stage.
>
> **The tools themselves behave identically under both Claude and Gemini** — the only difference is how many of them the client is willing to fire before checking in with you. Once you know the three fixes above, Gemini works end-to-end with every scenario JustThreed supports.
</details>

<details>
<summary><b>Ollama (free, local, offline)</b></summary>

1. Install Ollama from [ollama.com](https://ollama.com)
2. Pull a model: `ollama pull llama3` or `ollama pull mistral`
3. In the JustThreed panel inside Blender, select **Ollama** as your provider
4. Choose your model and start prompting — no internet needed

</details>

<details>
<summary><b>OpenRouter (free tier available)</b></summary>

1. Sign up at [openrouter.ai](https://openrouter.ai) and get a free API key
2. In the JustThreed panel, select **OpenRouter** as your provider
3. Enter your API key and choose a free model like `google/gemini-2.0-flash-thinking-exp:free`

</details>

---

## 💬 Example Prompts

### Basic modeling
```
Create a simple house with a red roof
Add windows to the front of the house
Make the walls white with a rough plaster texture
```

### Scene building
```
Set up a forest scene with 20 trees of varying heights
Add golden hour lighting from the left
Place a stone path leading to the house
```

### Asset integration
```
Search Poly Haven for a grass texture and apply it to the ground
Download a rock model from Poly Haven and scatter 15 copies randomly
```

### Advanced
```
Look at the current scene and create a Three.js version of it
Create 5 variations of this chair with different wood materials
Render the scene at 1920x1080 and save it to my desktop
```

---

## 🗂️ Project Structure

```
justthreed/
├── addon.py              # Blender addon (install this in Blender)
├── server.py             # MCP server (FastMCP tools)
├── tools/
│   ├── objects.py        # Create, modify, delete objects
│   ├── materials.py      # Material and texture control
│   ├── scene.py          # Lighting, cameras, environment
│   ├── assets.py         # Poly Haven integration
│   └── execute.py        # Python code execution
├── memory/
│   └── project.py        # Project memory and preferences
├── pyproject.toml        # Package config
└── README.md
```

---

## 🏗️ How It Works

```
AI Client (Claude / Cursor / Ollama / etc.)
       ↓
JustThreed MCP Server (server.py) — translates intent to commands
       ↓
Blender Addon (addon.py) — socket server on port 9876
       ↓
Blender (bpy API) — executes in real time
```

The Blender addon opens a TCP socket and waits for commands. The MCP server sits between your AI client and Blender, exposing tools that any AI model can call. When you type a prompt, the AI decides which tools to use and in what order, and Blender executes them instantly.

---

## 🔧 Available MCP Tools

| Tool | Description |
|---|---|
| `create_object` | Create mesh objects (cube, sphere, cylinder, plane, etc.) |
| `modify_object` | Move, rotate, scale, rename objects |
| `delete_object` | Remove objects from the scene |
| `set_material` | Apply colors and materials |
| `apply_texture` | Load and apply image textures |
| `get_scene_info` | Read current scene state |
| `set_lighting` | Add and configure lights |
| `set_camera` | Position and configure camera |
| `search_polyhaven` | Search Poly Haven asset library |
| `import_asset` | Import downloaded assets |
| `execute_code` | Run arbitrary Python in Blender |
| `render_scene` | Trigger a render |

---

## ⚠️ Known Limitations

- Complex artistic judgment (composition, style) still requires human input
- Very large scenes may require breaking prompts into smaller steps
- `execute_code` runs arbitrary Python — always save your work before using it
- Local models (Ollama) are less capable than cloud models for complex tasks

---

## 🤝 Contributing

Contributions are very welcome! Here is how to get started:

1. Fork this repository
2. Create a feature branch: `git checkout -b feature/my-new-tool`
3. Make your changes and test in Blender
4. Submit a pull request

### Ideas for contributions
- New MCP tools (animation, rigging, compositing)
- Support for more asset libraries (Sketchfab, AmbientCG)
- Better error handling and user feedback
- Documentation and video tutorials
- Testing on different OS and Blender versions

---

## 💰 Cost

**Free. Completely free.**

| What | Cost |
|---|---|
| JustThreed addon | ✅ Free |
| JustThreed MCP server | ✅ Free |
| Ollama local models | ✅ Free forever |
| OpenRouter free tier | ✅ Free |
| Blender | ✅ Free |
| Claude Pro / GPT-4 (optional) | ~$20/month (your choice) |

You only pay if you personally choose a premium AI provider. JustThreed itself will always be free.

---

## 🗺️ Roadmap

- [ ] Single addon install — no separate MCP server process
- [ ] Built-in model selector UI inside Blender
- [ ] Chat history panel in Blender sidebar
- [ ] Project memory — remembers your assets and preferences
- [ ] Poly Haven deep integration
- [ ] Animation and rigging support
- [ ] Sketchfab asset integration
- [ ] Batch rendering workflows
- [ ] Export pipeline — FBX, GLTF for Unity and Unreal Engine

---

## 📄 License

MIT License — free to use, modify, and distribute. See [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgements

- [Blender Foundation](https://www.blender.org/) for the open source 3D software
- [Anthropic](https://www.anthropic.com/) for the Model Context Protocol
- [ahujasid](https://github.com/ahujasid/blender-mcp) for the original BlenderMCP that inspired this project
- [Poly Haven](https://polyhaven.com/) for the free asset library
- [Ollama](https://ollama.com/) for making local AI models accessible to everyone

---

## 💝 Support this project

JustThreed is **free and open source forever** — the core 104 tools will always be MIT-licensed and nothing is gated behind a paywall.

If it saves you time or helps you ship a project, please consider supporting continued development. Every bit of support goes directly into more tools, more AI-client integrations, cross-platform testing, and keeping the project moving.

- 💖 **[Sponsor on GitHub](https://github.com/sponsors/Phanikondru)** — the `Sponsor` button at the top of this repository
- ⭐ **Star this repository** — it costs nothing, takes one click, and helps other artists find JustThreed
- 📣 **Share a render you made with JustThreed** on Twitter / X / LinkedIn and tag [@Phanikondru](https://x.com/Phanikondru) — social proof is the single most valuable thing you can give a new open-source project
- 🐛 **File bug reports and feature requests** — a good issue is worth more than a donation
- 💼 **Studio or agency pipeline?** — if you want JustThreed integrated into a production pipeline, or custom tools built on top of it, reach out via LinkedIn below

---

## ⭐ Community

Found a bug? Open an issue. Have an idea? Start a discussion. Want to contribute? Open a PR.

Built with ❤️ and open sourced for the community.

Connect with me:
- LinkedIn: [phanindhra-kondru](https://www.linkedin.com/in/phanindhra-kondru-436220205/)
- X: [@Phanikondru](https://x.com/Phanikondru)
