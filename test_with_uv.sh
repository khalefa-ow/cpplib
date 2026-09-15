#!/bin/bash
# Quick test script using uv

set -e

echo "========================================="
echo "Testing cpplib with uv"
echo "========================================="

echo ""
echo "1. Creating virtual environment..."
uv venv

echo ""
echo "2. Installing dependencies..."
uv pip install -e ".[dev]" > /dev/null 2>&1

echo ""
echo "3. Running tests..."
uv run pytest tests/ -v --tb=short

echo ""
echo "4. Code quality checks..."
echo "   - Formatting with black..."
uv run black cpplib/ --check > /dev/null 2>&1 || echo "     (formatting needed)"

echo "   - Linting with flake8..."
uv run flake8 cpplib/ > /dev/null 2>&1 || echo "     (lint issues found)"

echo "   - Type checking with mypy..."
uv run mypy cpplib/ > /dev/null 2>&1 || echo "     (type hints needed)"

echo ""
echo "========================================="
echo "✅ Testing complete!"
echo "========================================="
