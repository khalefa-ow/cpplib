#!/bin/bash
# Setup script to install all dependencies using uv

set -e

echo "🔧 Installing cpp-tools with agent dependencies..."
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed. Please install it first:"
    echo "   curl https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "✓ uv found: $(uv --version)"
echo ""

# Install the package with all dependencies
echo "📦 Installing cpplib with dev and agent extras..."
uv pip install -e ".[dev,agent]"

echo ""
echo "✅ Installation complete!"
echo ""
echo "📋 Verifying installation..."
uv run python3 -m agent.cli doctor

echo ""
echo "🎉 Setup successful! You can now run:"
echo "   ./run_pipeline_step_by_step.sh"
