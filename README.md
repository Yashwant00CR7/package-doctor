# Package Doctor

MCP server + Claude Code skill that prevents AI coding agents from using deprecated, conflicting, or stale Python packages and LLM model IDs.

> **Prevent the AI from shipping broken deps before they hit production.**

---

## What it does

| Trigger | What happens |
|---|---|
| `/pkg-doctor install <package>` | Checks if installing would break your venv. Flags deprecated packages. Shows ranked fix commands. |
| `/pkg-doctor diagnose` | Full venv health audit — all conflicts, deprecated packages, ordered fix list. |
| Model ID in code (`"gpt-4"`, `"claude-2"`) | Inline check: status, EOL date, successor model. |

### Example output

**`/pkg-doctor install torch`**
```
Package Doctor: torch
Status: CONFLICT
Latest: 2.3.1

Conflicts:
- torch already installed at 2.1.0

Fix options (pick one):
1. pip install "torch"

Warnings:
- torch is already installed at a different version
```

**`/pkg-doctor diagnose`**
```
Package Doctor: Venv Health Report
Installed: 47 packages

Conflicts (2):
- docutils: requires <0.18,>=0.14, have 0.18.1 [needed by sphinx]
  Fix: pip install "docutils<0.18,>=0.14"
- urllib3: requires <3,>=1.21.1, have 3.0.0 [needed by requests]
  Fix: pip install "urllib3<3,>=1.21.1"

Deprecated (1):
- langchain-community — use langchain-core

Ranked fixes:
1. pip install "docutils<0.18,>=0.14"
2. pip install "urllib3<3,>=1.21.1"
```

**Model check (inline)**
```
Package Doctor: Model Warning
Model: claude-2 [anthropic]
Status: DEPRECATED / EOL 2025-03-01
Use instead: claude-opus-4-7
Source: https://docs.anthropic.com/en/docs/about-claude/models
```

---

## Architecture

```
Claude Code Skill (.claude/skills/package-doctor.md)
  Agent runs: pip freeze, pip check (never inside MCP)
  Agent detects: active venv, package manager (uv/conda/pip)
          ↓
Package Doctor MCP Server (package-doctor-mcp)
  ├── Core tier (always works — no external MCPs required)
  │     PyPI JSON API (httpx) + SQLite cache (~/.package-doctor/cache.db)
  └── Optional enrichment (graceful degradation if absent)
        Firecrawl → fresh model doc crawls
```

**Key constraint:** The MCP server never runs shell commands. The agent collects `pip freeze` / `pip check` output and passes it as strings. All fix commands are returned for user confirmation — never auto-executed.

---

## Quick start

### 1. Install the MCP server

```bash
# Clone
git clone https://github.com/yashwanth-alapati/ai-package-doctor
cd ai-package-doctor

# Install with uv (recommended)
uv sync

# Or with pip
pip install -e .
```

### 2. Add to Claude Code

Add to your `~/.claude.json` (global) or `.claude/settings.json` (project):

```json
{
  "mcpServers": {
    "package-doctor": {
      "command": "package-doctor-mcp",
      "type": "stdio"
    }
  }
}
```

If using uv without installing globally:

```json
{
  "mcpServers": {
    "package-doctor": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/ai-package-doctor", "package-doctor-mcp"],
      "type": "stdio"
    }
  }
}
```

### 3. Install the Claude Code skill

Copy the skill file to your Claude Code skills directory:

```bash
# macOS/Linux
cp .claude/skills/package-doctor.md ~/.claude/skills/

# Windows
copy .claude\skills\package-doctor.md %USERPROFILE%\.claude\skills\
```

Or reference it from your project's `.claude/skills/` directory — Claude Code picks it up automatically.

### 4. Verify

Restart Claude Code, then run:

```
/pkg-doctor diagnose
```

If the MCP server isn't running, Claude will show:
```
Start the MCP server: uv run package-doctor-mcp
```

---

## MCP Tools (direct API use)

The four tools can be called directly from any MCP-compatible client:

### `check_package`

```json
{
  "package_name": "langchain-community"
}
```

Returns: `{ "name": "langchain-community", "status": "deprecated", "alternative": "langchain-core", "latest_version": "0.3.0", ... }`

### `check_pre_install`

```json
{
  "package_name": "torch",
  "pip_freeze": "numpy==1.23.0\nscipy==1.11.0\n...",
  "package_manager": "uv"
}
```

Returns: `{ "would_conflict": false, "conflicting_packages": [], "resolution_commands": [], ... }`

### `check_venv_health`

```json
{
  "pip_freeze": "sphinx==4.3.0\ndocutils==0.18.1\n...",
  "pip_check": "sphinx 4.3.0 requires docutils<0.18,>=0.14, but you have docutils 0.18.1 which is incompatible."
}
```

Returns full conflict map with ranked fix commands.

### `check_model_version`

```json
{
  "model_id": "gpt-3.5-turbo-0301"
}
```

Returns: `{ "status": "error", "successor_model": "gpt-4o-mini", "eol_date": "2024-09-13", ... }`

---

## Caching

- Package metadata: cached 24h at `~/.package-doctor/cache.db`
- Model status: cached 6h, re-crawled on stale
- No background daemon — checks happen on demand

---

## Development

```bash
# Install with dev deps
uv sync --extra dev

# Run tests
pytest tests/ -v

# Lint
uv run ruff format && uv run ruff check

# Run MCP server locally (stdio)
package-doctor-mcp
```

### Running tests

```
pytest tests/test_core.py -v

tests/test_core.py::test_fetch_active_package PASSED
tests/test_core.py::test_fetch_deprecated_package PASSED
tests/test_core.py::test_fetch_missing_package PASSED
tests/test_core.py::test_fetch_network_failure PASSED
tests/test_core.py::test_parse_pip_freeze_basic PASSED
tests/test_core.py::test_parse_pip_check_conflict PASSED
tests/test_core.py::test_parse_pip_check_clean PASSED
tests/test_core.py::test_build_fix_commands_pip PASSED
tests/test_core.py::test_build_fix_commands_uv PASSED
tests/test_core.py::test_pre_install_already_installed PASSED
tests/test_core.py::test_pre_install_clean PASSED
tests/test_core.py::test_model_known_removed PASSED
tests/test_core.py::test_model_known_warning PASSED
tests/test_core.py::test_model_claude_removed PASSED
tests/test_core.py::test_model_detect_provider_openai PASSED
tests/test_core.py::test_model_detect_provider_anthropic PASSED
```

---

## Project layout

```
ai-package-doctor/
├── src/package_doctor/
│   ├── models/          # Pydantic response models
│   ├── collectors/
│   │   ├── pypi.py      # PyPI JSON API + deprecation detection
│   │   └── model_checker.py  # LLM model version checker
│   ├── conflict_analyzer.py  # pip freeze/check parser + fix ranker
│   ├── store.py         # SQLite cache with TTL
│   └── mcp_server/      # MCP server — 4 tools wired up
├── tests/
│   └── test_core.py
├── .claude/skills/
│   └── package-doctor.md   # Claude Code skill file
└── pyproject.toml
```

---

## License

MIT
