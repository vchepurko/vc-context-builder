# 🤖 AI Agent Standard Operating Procedure (SOP)

## Three ways to query (pick the lightest one)

1. **MCP-aware agent** (Claude Code, Cursor, Codex CLI ≥ 0.X, Continue):
   register the MCP server (see `.ai-context/MCP_SETUP.md`). Call tools
   directly — JSON files never enter your context.

2. **Generic LLM with shell access**: invoke the CLI from a tool —
   `vc-context find <name>`, `vc-context role webhook`. Each call
   returns ~100–500 bytes of JSON.

3. **No tools, only file reading**: fall back to the JSON artifacts
   (`agent_root.json` → `agent_symbols.json` → `_module_map.json`).
   This is the universal-but-most-token-expensive path.

In all three cases the cardinal rule still applies: read narrowly.

## System Architecture
This repository utilizes `vc-context-builder` to maintain a real-time, hierarchical Retrieval-Augmented Generation (RAG) context map.

The architecture is modular and uses dynamic component-based parsers located in the `/parsers` directory. It automatically extracts context from:
* **Source Code:** Python, PHP, TypeScript, JavaScript (identifying exports, classes, and dependencies).
* **DevOps & Infrastructure:** Dockerfiles, docker-compose.yml, Makefiles, and GitHub Actions (identifying base images, exposed ports, and build targets).

## Agent Workflow (Read Carefully):

1. **DISCOVER (Root Context):** Always start by reading `agent_root.json` in the project root. This file provides the high-level map of all scanned directories.

2. **LOCATE (Module Context):** Navigate to specific directories and read their local `_module_map.json`. This file contains the extracted AST/Regex metadata (classes, functions, dependencies) for the files in that specific folder.

3. **MODIFY:** Edit the actual source code or infrastructure files as requested by the user.

4. **REGENERATE (Self-Healing Context):** * **Local Updates:** If you need to refresh your context mid-task after creating/modifying files, run `python agent_map.py` in the terminal to rebuild the JSON maps.
    * **Commits:** You do NOT need to manually add JSON maps to Git. A local `pre-commit` hook and a cloud GitHub Action are configured to automatically rebuild and stage the context graphs on every commit.

## Constraints
* **DO NOT** manually edit `_module_map.json` or `agent_root.json`. They are strictly machine-generated.
* **DO NOT** modify the `setup_hook.sh` unless explicitly requested to alter CI/CD behavior.
* If you encounter an unsupported file type, you can extend the system by creating a new subclass of `BaseParser` inside the `/parsers` directory.