#!/bin/bash

# Ensure the git hooks directory exists
mkdir -p .git/hooks

# Generate the pre-commit hook script
cat << 'EOF' > .git/hooks/pre-commit
#!/bin/bash

echo "🧪 Running unit tests..."
# Run all tests in the tests/ directory
python3 -m unittest discover tests/

# If tests fail, abort the commit
if [ $? -ne 0 ]; then
  echo "❌ Tests failed! Commit aborted. Please fix the code."
  exit 1
fi

echo "🤖 vc-context-builder: Updating Agent Context Graph..."

# Run the context builder core
python3 agent_map.py

# Stage the newly generated or updated context maps
git add "**/*_module_map.json" "agent_root.json" "AGENT_README.md" > /dev/null 2>&1

echo "✅ Context Graph updated and staged!"
EOF

# Make the hook executable
chmod +x .git/hooks/pre-commit

echo "🎉 Pre-commit hook with testing enabled installed successfully!"
