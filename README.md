# 🤖 vc-context-builder

A zero-dependency, auto-updating Context Graph Builder for LLM coding agents (Cursor, Copilot, Claude).

It scans your project, parses ASTs (Abstract Syntax Trees) and heuristics, and builds a real-time `_module_map.json` for every directory. This gives AI agents a perfect, hallucination-free understanding of your project's architecture, dependencies, and available functions/classes.

## 🌟 Features
* **Zero Dependencies:** Written in pure Python. No `node_modules`, no external libraries.
* **Deep Parsing:** * `Python` (Native AST parsing)
    * `PHP` (WordPress/WooCommerce hooks, traits, interfaces)
    * `JS/TS` (React/Angular syntax, dynamic imports)
    * `DevOps` (Dockerfiles, Makefiles, GitHub Actions)
* **Self-Healing:** Automatically ignores unchanged directories using `mtime` and file composition checks.
* **Auto-Updating:** Integrates directly into your `pre-commit` workflow.

## 🚀 Installation (Git Submodule)

The best way to use this tool is to add it as a Git submodule to your main project. This keeps the builder isolated and easily updatable.

1. Navigate to your target project root:
   ```bash
   cd your-project-root
   ```
   
2. Add the context builder as a submodule:
   ```bash
   git submodule add [https://github.com/YOUR_USERNAME/vc-context-builder.git](https://github.com/YOUR_USERNAME/vc-context-builder.git) .ai-context
    ```
   
3. Run the automated installer:
   ```bash
   ./.ai-context/install.sh
   ```

### What does the installer do?
* **Creates a pre-commit git hook** in your parent project.
* **Generates the initial AI context maps** (`agent_root.json` and `_module_map.json` files).
* **Automatically adds the `.ai-context` folder to your `.gitignore`** to prevent recursive mapping.

---

### 🛠 How It Works
Once installed, you don't need to do anything manually.
Every time you run `git commit` in your project, the builder will automatically run in the background, update the JSON context maps for any modified folders, and stage them with your commit.

---

### 🔌 Extending Parsers
Want to add support for **Go**, **Ruby**, or **Rust**?
Simply create a new file in `.ai-context/parsers/`, inherit from `BaseParser`, define your `extensions`, and the system will auto-register it.