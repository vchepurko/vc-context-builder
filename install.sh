#!/bin/bash

# Get the directory where the builder is located (e.g., .ai-context)
BUILDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The target project is the parent directory
PROJECT_ROOT="$(dirname "$BUILDER_DIR")"
# Get the folder name of the builder (to make paths dynamic)
RELATIVE_DIR=$(basename "$BUILDER_DIR")

# Navigate to the target project root
cd "$PROJECT_ROOT" || exit

echo "🤖 Installing vc-context-builder into parent project..."

# 1. Setup git hooks directory
mkdir -p .git/hooks

# 2. Generate the pre-commit hook targeting the submodule path
cat << EOF > .git/hooks/pre-commit
#!/bin/bash

echo "🤖 vc-context-builder: Updating Agent Context Graph..."

# Run the context builder from the submodule directory
python3 $RELATIVE_DIR/agent_map.py

# Stage the newly generated or updated context maps
git add "**/*_module_map.json" "agent_root.json" "AGENT_README.md" > /dev/null 2>&1

echo "✅ Context Graph updated and staged!"
EOF

chmod +x .git/hooks/pre-commit

# 3. Add builder directory to .gitignore so maps inside it don't conflict,
# but only if it's not already there
if ! grep -q "$RELATIVE_DIR/" .gitignore 2>/dev/null; then
    echo "$RELATIVE_DIR/" >> .gitignore
fi

# 4. Run initial scan
python3 "$RELATIVE_DIR/agent_map.py"

echo "🎉 Installation complete! The context builder will now run automatically on every commit."