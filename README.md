<div align="center">

<!-- TODO: replace with a real logo (recommend 160–240px square). Drop the file at .github/assets/logo.png and uncomment. -->
<!-- <img src=".github/assets/logo.png" alt="JustThreed" width="180" /> -->

<h1>JustThreed</h1>

<p><i>Product mockups from plain English — built for designers, marketers, and founders, not just 3D artists.<br/>Describe your product, get a photorealistic render. No Blender experience needed.<br/>Works with Claude, Gemini, ChatGPT, Cursor, VSCode, and local models via Ollama.<br/>One addon. Zero config. Fully free and open source.</i></p>

<p>
  <a href="https://github.com/Phanikondru/justthreed/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License" /></a>
  <img src="https://img.shields.io/badge/Blender-4.5%2B-orange?style=flat-square" alt="Blender 4.5+" />
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square" alt="Python 3.11+" />
  <a href="https://pypi.org/project/justthreed/"><img src="https://img.shields.io/pypi/v/justthreed?style=flat-square&color=blue" alt="PyPI" /></a>
  <a href="https://github.com/sponsors/Phanikondru"><img src="https://img.shields.io/github/sponsors/Phanikondru?label=Sponsor&logo=GitHub&color=ea4aaa&style=flat-square" alt="Sponsor" /></a>
  <a href="https://github.com/Phanikondru/justthreed/stargazers"><img src="https://img.shields.io/github/stars/Phanikondru/justthreed?style=flat-square&color=yellow" alt="Stars" /></a>
</p>

<p>
  <a href="#local-setup"><b>Get started</b></a> ·
  <a href="#what-you-can-do"><b>What you can do</b></a> ·
  <a href="#limitations"><b>Limitations</b></a> ·
  <a href="https://github.com/sponsors/Phanikondru"><b>Sponsor</b></a> ·
  <a href="https://x.com/Phanikondru"><b>@Phanikondru</b></a>
</p>

<img src="public/cream_jar_final_production.png" alt="Cream jar product render — built entirely with JustThreed" width="760" />

</div>

---

## What is JustThreed?

JustThreed lets **anyone** — designers, marketers, founders, freelancers — create professional product mockups using just natural language. You don't need to know Blender, 3D modeling, or scripting. Describe your product in plain English, and JustThreed builds it in Blender: the shape, materials, studio lighting, camera, and final render.

**Our initial focus is product mockups.** Cream jars, perfume bottles, candles, packaging, skincare — any physical product you need to visualize. The cream jar hero image above was built entirely with prompts like these:

```
"Create a cream jar with a glossy pink body and a dark glass lid"
"Set up a marble surface underneath with soft studio lighting"
"Add three more jars scattered around the hero jar at different angles"
"Render the final product shot at high quality with depth of field"
```

Under the hood, JustThreed connects Blender to any AI model through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). You pick the AI client you already use — Claude, Gemini, ChatGPT, Cursor, or a free local model via Ollama — and JustThreed gives it full control over Blender.

---

## What makes JustThreed different

| Feature | JustThreed | Existing tools |
|---|---|---|
| Target audience | Anyone — no 3D skills required | Built for 3D artists |
| Primary use case | Product mockups from text | General 3D scripting |
| Setup | Single addon, zero config | Multiple steps, config files |
| AI models | Claude, Gemini, GPT, Ollama, Cursor | Mostly Claude-only |
| Chat panel | Built into Blender sidebar | Separate app required |
| Cost | 100% free | Some paid tiers |
| License | MIT open source | Mixed |

---

## Screenshots & demos

<div align="center">
<img src="public/cream_jar_final_production.png" alt="Cream jar product render" width="640" />
<br/>
<i>Cream jar product mockup — modeled, textured, lit, and rendered entirely through natural language prompts. No Blender experience needed.</i>
</div>

> More product renders coming soon — skincare, packaging, bottles, candles, and full walkthrough videos.
>
> **Built something cool with JustThreed?** Share it on X / LinkedIn and tag [@Phanikondru](https://x.com/Phanikondru) — we'll feature community renders here with credit.

---

## How it works

```
AI Client (Claude / Gemini / Cursor / Ollama / etc.)
       ↓
JustThreed MCP Server (server.py) — translates intent to commands
       ↓
Blender Addon (addon.py) — socket server on port 9876
       ↓
Blender (bpy API) — executes in real time
```

The Blender addon opens a TCP socket and waits for commands. The MCP server sits between your AI client and Blender, exposing tools that any AI model can call. When you type a prompt, the AI decides which tools to use and in what order, and Blender executes them instantly.

---

## Local setup

> This is the current install path. Once JustThreed is published to the Blender Extensions platform, a one-click install will be available — see [Blender Extension — coming soon](#blender-extension--coming-soon) below.

### Step 0 — How the MCP server is installed (you probably don't need to do anything)

The JustThreed MCP server is [published on PyPI](https://pypi.org/project/justthreed/). When you configure your AI client in Step 4, the `uvx justthreed` command automatically downloads and runs it from PyPI — no manual `pip install` needed.

If you prefer a manual install for any reason:

```bash
pip install justthreed
```

Then replace `"command": "uvx"` with `"command": "justthreed"` in the AI client configs below.

### Step 1 — Install uv

**Windows:**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2 — Install the Blender addon

1. Download `addon.py` from this repository
2. Open Blender → **Edit** → **Preferences** → **Add-ons**
3. Click **Install from Disk** and select `addon.py`
4. Enable the addon by ticking the checkbox next to **Interface: JustThreed**

### Step 3 — Start the MCP socket server in Blender

> ⚠️ **This is a manual step, and you have to do it every single time you open Blender.** JustThreed's Blender addon opens a TCP socket on `localhost:9876` that your AI client talks to. The socket is **not** started automatically when Blender launches — you have to click the button each time. If you skip this step, your AI client will still register the JustThreed MCP server fine (no config error), but every tool call will fail with a "connection refused" error because nothing is actually listening on port 9876. This is the single most common "it was working yesterday, why isn't it working today?" cause.

**To start it:**

1. In Blender, press **N** in the 3D viewport to open the right-hand side panel
2. Click the **JustThreed** tab
3. Click **Start MCP Server**
4. Watch Blender's system console for the message `JustThreed MCP Server started on port 9876`. The system console is hidden by default — on Windows, enable it via **Window → Toggle System Console**; on macOS and Linux, launch Blender from a terminal (`/Applications/Blender.app/Contents/MacOS/Blender` on macOS) so console output goes to that terminal.

**To verify from outside Blender** (useful when troubleshooting a stuck setup):

```bash
lsof -nP -iTCP:9876
```

If you see a line mentioning `Blender` with `LISTEN`, the socket is live and your AI client can talk to it. If the command returns nothing, the server is not running — go back to step 1. The check works identically on macOS and Linux; on Windows use `netstat -ano | findstr :9876`.

**To stop it:** click **Stop MCP Server** in the same panel, or just quit Blender — the socket is cleaned up automatically on exit.

**Every new Blender session requires repeating these steps.** There is no "start on launch" option yet — auto-start is a tracked feature request on the [Roadmap](#contributing), and a good first contribution for anyone wanting to help out. Until then: open Blender → click Start MCP Server → *then* launch your AI client.

### Step 4 — Connect your AI client

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
<summary><b>Claude Code (CLI)</b></summary>

> **Before you run these commands:** Blender must be open, the JustThreed addon enabled, and the MCP socket server started from the sidebar (Step 3 above). Claude Code talks to JustThreed, which talks to Blender via a local socket — if Blender's socket isn't listening on port 9876 you'll see connection errors no matter how the client is configured.

**1. Register JustThreed as a user-scoped MCP server** (available in every project, not just one):

```bash
claude mcp add --scope user justthreed -- uvx justthreed
```

Alternatively, to scope it to a single project, create `.mcp.json` in the project root:

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

**2. Verify registration** (in any terminal, no `claude` session needed):

```bash
claude mcp list
```

You should see a line like `justthreed: uvx justthreed - ✓ Connected`. If it says `✗ Failed` or `Needs authentication`, go back and start the socket server inside Blender.

**3. Restart any running Claude Code session.** ⚠️ This is the step most people miss — **MCP servers are loaded once at session startup**, so if you already had `claude` running when you ran `mcp add`, the new server is registered but not active in that session. Exit with `/exit` and relaunch. Inside the fresh session, run `/mcp` to see the JustThreed tools listed.

Claude Code chains tool calls aggressively, so you can usually build an entire scene in a single prompt — see the [Limitations](#limitations) section for details.
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

In Cursor, also raise **Settings → Features → Chat → Max tool calls per request** to 50–100 so it doesn't stop mid-build.
</details>

<details>
<summary><b>Gemini CLI</b></summary>

> **Before you start:** Blender must be open with the JustThreed addon enabled and the MCP socket server started from the sidebar (Step 3 above). If port 9876 isn't listening inside Blender, Gemini CLI will connect to JustThreed but every tool call will fail with a socket error.

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

## Blender extension — coming soon

> 🚧 **One-click install directly from Blender is on the way.**
>
> JustThreed has not yet been submitted to the [Blender Extensions platform](https://extensions.blender.org/). For now, the install path is the "Local setup" steps above — download `addon.py`, install from disk, and start the MCP server from the sidebar.
>
> When the extension is published, you'll be able to:
>
> - Install it in two clicks from **Edit → Preferences → Get Extensions**
> - Get auto-updates with every new release — no more re-downloading `addon.py`
> - Skip the `uv` install step entirely on most setups
>
> This section will be updated with the extension link and the new install flow the day it goes live. ⭐ Star the repo to get notified via the GitHub Releases feed.

---

## What you can do

### Features

- **Product mockups from text** — describe a product and get a render-ready 3D model with materials, lighting, and camera
- **Studio-quality rendering** — three-point lighting, depth of field, Cycles/EEVEE, color management — all set up through prompts
- **Material & texture control** — PBR materials, glass, metal, ceramic, wood — apply and tweak via natural language
- **Batch variations** — "create 5 color variations of this jar" generates them all in one prompt
- **Scene management** — control lighting, cameras, backgrounds, and composition
- **Two-way communication** — AI reads your current scene before making changes, so it never duplicates or overwrites
- **Save and resume** — save your scene to a `.blend` file tonight, pick it up in a new chat tomorrow
- **Asset library integration** — pull textures and models from Poly Haven directly
- **Built-in chat panel** — prompt directly from Blender's sidebar (no app switching)
- **Python code execution** — run arbitrary Blender Python scripts via AI for anything the built-in tools don't cover

### Example prompts

**Product mockups** (the primary use case right now)
```
Create a skincare bottle with a matte white body and rose gold cap
Build a coffee mug with a ceramic glaze material on a wooden table
Design a perfume bottle with a glass body and metallic spray nozzle
Create a candle jar with a frosted glass body and a wooden lid
```

**Studio setup**
```
Set up three-point lighting for a product shot
Add a marble surface with soft shadows underneath the product
Create a seamless white backdrop with rim lighting
Add depth of field focused on the hero product
```

**Variations and composition**
```
Duplicate the bottle and arrange 4 copies in a diagonal layout
Create 3 color variations of the jar — pink, mint, and lavender
Scatter the products naturally on the surface like a flatlay
```

**Render and export**
```
Render the final product shot at 1920x1080 with high samples
Set up the camera at a 45-degree hero angle
Switch to Cycles for a photorealistic final render
Save the scene so I can pick it up tomorrow
```

### Supported AI clients

Works with any MCP-compatible AI client:

- **Claude Desktop** (Anthropic)
- **Claude Code (CLI)** — the most agentic option, chains the whole build in one prompt
- **Cursor** / **VSCode** with MCP extension
- **Gemini CLI** (Google)
- **ChatGPT** with MCP support
- **Ollama** — run Llama 3, Mistral, Qwen, DeepSeek locally for **100% free, offline use**
- **OpenRouter** — access free models like Gemini Flash, DeepSeek R1

> 💡 **Want zero cost forever?** Use Ollama with a local model. No API key, no internet, no limits.

### Available MCP tools

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
| `save_blend_file` | Save the current scene to a `.blend` file |
| `open_blend_file` | Load a `.blend` file (great for resuming work in a new chat) |

---

## Limitations

### Tool-call limits per AI client — read this before your first complex scene

> It is the single biggest source of "why did it stop halfway?" confusion for new users, and it applies to **every** AI client, not just JustThreed.

Every AI client that speaks MCP has a practical limit on **how many tools it can call inside a single conversational turn** before it pauses and checks in with you. This is not a JustThreed limit — it is part of each client's tool-use policy. It matters because a finished product render often needs **15 to 40 tool calls** (scene info, primitives, modifiers, materials, shader nodes, lighting, camera, render settings, compositor, render_and_show, …).

**What you will see per client:**

| Client | Type | Typical per-turn tool-call budget | Default behavior |
|---|---|---|---|
| **Claude Desktop** | Chat app | ~10–25 tool calls, then pauses | Fires a batch, summarizes progress, asks "should I continue?". The hardest per-turn cap of any client — feels the most "stuck" on big scenes. |
| **Claude Code (CLI)** | Terminal agent | ~100+ tool calls per turn, effectively context-bound | Chains aggressively until the task is done or the context window fills. Rarely pauses mid-build. The most hands-off option for long scenes. |
| **Gemini CLI** | Terminal agent | 1 tool call, then pauses | Runs the **first** tool and stops, waiting for confirmation. This is a policy default, not a hard limit. Use `/yolo` mode or the "proceed" instruction above to unlock full chaining. |
| **Cursor** | IDE | ~25 tool calls per request (configurable) | Setting: `Cursor Settings → Features → Chat → Max tool calls per request`. Raise it to 50–100 for JustThreed. |
| **VSCode + Copilot Chat / Continue** | IDE | Varies | Some chain freely, some pause per tool. Check your extension's agent-mode settings. |
| **Ollama / local MCP clients** | Local | No remote limit | Only bounded by your local model's context window. Smaller local models (7B–13B) often forget mid-plan and need smaller prompts regardless. |

#### CLI vs desktop — the short version

**CLI clients have way more headroom than desktop chat apps.** Claude Code in the terminal and Gemini CLI (once `/yolo` is on) will happily fire 50–100+ JustThreed tools in a single turn without asking — more than enough for a fully-lit, fully-materialed hero render in one prompt. Claude Desktop, by contrast, almost always pauses before you get there.

**But "CLI = no limits" is not quite true.** Even CLI agents eventually hit:

- **Context window limits** — every tool call and its result consume tokens. On a 200K context, you get roughly 80–150 JustThreed tool calls before the model starts forgetting earlier steps.
- **Per-turn iteration caps** — Claude Code has an internal cap (configurable) on how many tools it will run before yielding back to the user. It's high, but not infinite.
- **Rate limits on the provider side** — Anthropic and Google both throttle extremely long agentic runs on heavy usage.

**Practical rule of thumb for JustThreed:**

| If you use... | You can safely prompt... |
|---|---|
| Claude Code CLI | A full product render in one prompt (materials + lights + camera + render). |
| Gemini CLI with `/yolo` | Same — full scene in one prompt. |
| Claude Desktop | 2–3 stages per prompt. Use the "continue" pattern below when it pauses. |
| Cursor / VSCode | 2–3 stages per prompt unless you raise the max-tool-calls setting. |
| Ollama (local) | 1 stage per prompt. Local models lose focus faster than cloud models. |

**None of this blocks you from building anything** — it just means you work in **checkpoints** instead of one giant prompt. Three patterns below.

#### Pattern 1 — "Just keep going"

When the AI pauses mid-build, reply with one line:

```
Continue from where you left off. Call get_scene_info first so you
know exactly what is already in the scene, then keep going with the
remaining steps.
```

`get_scene_info` returns the **complete** scene state (every object, every material, every modifier, every collection), so the AI can pick up exactly where it stopped without guessing or repeating work. This pattern alone handles 90% of tool-limit pauses.

#### Pattern 2 — "Save and resume in a new chat"

For very long builds (40+ tool calls) or when you want to stop for the night and come back tomorrow, use `.blend` files as checkpoints. JustThreed's `save_blend_file` + `open_blend_file` pair is built for this — and because the extension registers a persistent load-post handler, **the MCP server survives file reloads**, so you can chain this indefinitely.

**Chat 1 — build the base, then save:**
```
Build the bronze spirits bottle described above. When you're done, call
save_blend_file with path "~/Desktop/bottle.blend".
```

**Chat 2 — fresh conversation, resume where you left off:**
```
Call open_blend_file with path "~/Desktop/bottle.blend", then
get_scene_info so you can see what's already built. Then add the
three-point lighting and render.
```

No tool-limit budget is wasted on re-creating anything — the new chat starts from the saved scene state.

#### Pattern 3 — "Break the prompt into stages" (the reliable default)

The most predictable way to build anything complex is to break the work into **3–5 stages**, one prompt per stage, each ending with a `render_and_show`. This avoids hitting any limits at all and **also gives you better results**, because seeing a mid-state render lets you catch mistakes early instead of after 40 tool calls of compounding errors.

**Typical staging for a hero product render:**

1. **Shape** — primitives + edit-mode modeling + modifiers → `render_and_show`
2. **Materials** — shader node graph, PBR, textures → `render_and_show`
3. **Lighting + camera** — studio preset, DoF, f-stop → `render_and_show`
4. **Render + post** — engine, samples, color management, compositor → final `render_and_show`

This is how professional Blender artists actually work anyway — lighting the scene before the materials are done is a waste of time. JustThreed just formalizes the rhythm.

#### The one-line takeaway

> **If the AI pauses mid-build, say *"continue — call get_scene_info first"*. If you are starting a big scene, break it into 3–5 stages and end each one with `render_and_show`. If you want to pick up tomorrow, `save_blend_file` tonight and `open_blend_file` tomorrow.**

### Other known limitations

- **Complex artistic judgment** (composition, style) still requires human input — the AI can execute, but it can't replace taste.
- **Very large scenes** may require breaking prompts into smaller steps (see the three patterns above).
- **`execute_code` runs arbitrary Python** — always save your work before using it.
- **Local models (Ollama)** are less capable than cloud models for complex tasks. Expect to hand-hold smaller models more than Claude / Gemini.
- **Not yet published** on the Blender Extensions platform — for now you install `addon.py` manually. See the "coming soon" section above.

---

## Requirements

- Blender 4.5.0 or newer (tested on 4.5.0 — should work on any later version)
- Python 3.11 or newer (bundled with Blender 4.5+)
- `uv` package manager
- One of the supported AI clients listed above

---

## Project structure

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

## Contributing

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

### Roadmap
- [ ] Single addon install — no separate MCP server process
- [ ] Publish to the Blender Extensions platform (one-click install)
- [ ] Built-in model selector UI inside Blender
- [ ] Chat history panel in Blender sidebar
- [ ] Deeper Poly Haven integration
- [ ] Animation and rigging support
- [ ] Sketchfab asset integration
- [ ] Batch rendering workflows
- [ ] Export pipeline — FBX, GLTF for Unity and Unreal Engine

---

## Support this project

JustThreed is **free and open source forever** — the core tools will always be MIT-licensed and nothing is gated behind a paywall.

| What | Cost |
|---|---|
| JustThreed addon | ✅ Free |
| JustThreed MCP server | ✅ Free |
| Ollama local models | ✅ Free forever |
| OpenRouter free tier | ✅ Free |
| Blender | ✅ Free |
| Claude Pro / GPT-4 (optional) | ~$20/month (your choice) |

You only pay if you personally choose a premium AI provider. JustThreed itself will always be free.

If it saves you time or helps you ship a project, please consider supporting continued development. Every bit of support goes directly into more tools, more AI-client integrations, cross-platform testing, and keeping the project moving.

- 💖 **[Sponsor on GitHub](https://github.com/sponsors/Phanikondru)** — the `Sponsor` button at the top of this repository
- ⭐ **Star this repository** — it costs nothing, takes one click, and helps other artists find JustThreed
- 📣 **Share a render you made with JustThreed** on Twitter / X / LinkedIn and tag [@Phanikondru](https://x.com/Phanikondru) — social proof is the single most valuable thing you can give a new open-source project
- 🐛 **File bug reports and feature requests** — a good issue is worth more than a donation
- 💼 **Studio or agency pipeline?** — if you want JustThreed integrated into a production pipeline, or custom tools built on top of it, reach out via LinkedIn below

---

## License

MIT License — free to use, modify, and distribute. See [LICENSE](LICENSE) for details.

---

## Acknowledgements

- [Blender Foundation](https://www.blender.org/) for the open source 3D software
- [Anthropic](https://www.anthropic.com/) for the Model Context Protocol
- [ahujasid](https://github.com/ahujasid/blender-mcp) for the original BlenderMCP that inspired this project
- [Poly Haven](https://polyhaven.com/) for the free asset library
- [Ollama](https://ollama.com/) for making local AI models accessible to everyone

---

## Community

Found a bug? Open an issue. Have an idea? Start a discussion. Want to contribute? Open a PR.

Built with ❤️ and open sourced for the community.

Connect with me:
- LinkedIn: [phanindhra-kondru](https://www.linkedin.com/in/phanindhra-kondru-436220205/)
- X: [@Phanikondru](https://x.com/Phanikondru)
