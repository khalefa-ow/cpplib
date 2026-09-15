# Testing cpplib with uv

Fast Python package management and testing using `uv`.

## Installation

### 1. Install uv (if not already installed)

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with brew (macOS)
brew install uv

# Or with pip
pip install uv
```

Verify installation:
```bash
uv --version
```

## Quick Start with uv

### 1. Clone and Setup

```bash
git clone https://github.com/khalefa-ow/cpplib.git
cd cpplib

# Create virtual environment with uv
uv venv

# Activate it
source .venv/bin/activate  # Linux/macOS
# or
.venv\Scripts\activate  # Windows
```

### 2. Install Dependencies with uv

```bash
# Install in development mode
uv pip install -e ".[dev]"

# Or install requirements
uv pip install -r requirements.txt
```

### 3. Run Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_parser.py -v

# Run with coverage
uv run pytest tests/ --cov=cpplib --cov-report=html
```

## Common uv Commands

### Environment Management

```bash
# Create venv
uv venv

# Create venv with specific Python version
uv venv --python 3.11

# List installed packages
uv pip list

# Show package details
uv pip show pytest
```

### Package Management

```bash
# Install single package
uv pip install tree-sitter

# Install from requirements.txt
uv pip install -r requirements.txt

# Sync requirements exactly
uv pip sync requirements.txt

# Compile requirements
uv pip compile requirements.txt -o requirements-compiled.txt

# Update all packages
uv pip install --upgrade pip
```

### Running Code

```bash
# Run Python script
uv run python script.py

# Run pytest
uv run pytest tests/

# Run with arguments
uv run pytest tests/test_parser.py -v -s
```

## Full Test Workflow with uv

### 1. Fresh Setup

```bash
# Remove old venv
rm -rf .venv

# Create new venv
uv venv

# Install dependencies
uv pip install -e ".[dev]"
```

### 2. Run Full Test Suite

```bash
# Run all tests
uv run pytest tests/ -v

# Show test results
uv run pytest tests/ -v --tb=short

# Run with coverage report
uv run pytest tests/ -v --cov=cpplib --cov-report=term-missing
```

### 3. Code Quality Checks

```bash
# Format code
uv run black cpplib/

# Lint code
uv run flake8 cpplib/

# Type check
uv run mypy cpplib/
```

### 4. Test Specific Scenarios

```bash
# Test parser only
uv run pytest tests/test_parser.py -v

# Test with real C++ project
uv run python /tmp/test_gen_project.py

# Test semantic layer
uv run pytest tests/test_semantic.py -v

# Test extraction
uv run pytest tests/test_extraction.py -v

# Test generation
uv run pytest tests/test_generation.py -v

# Test validation
uv run pytest tests/test_validator.py -v
```

## Testing Real Projects with uv

### Test on /home/mk/program/gen

```bash
# Create test script
cat > test_with_uv.py << 'EOF'
import sys
sys.path.insert(0, '/home/mk/cpp_tools')

from cpplib import CPPCodebase

codebase = CPPCodebase("/home/mk/program/gen")
functions = codebase.get_all_functions()
classes = codebase.get_all_classes()

print(f"✓ Found {len(functions)} functions")
print(f"✓ Found {len(classes)} classes")

is_valid, msg = codebase.validate(clean=False)
print(f"✓ Validation: {is_valid}")
EOF

# Run with uv
uv run python test_with_uv.py
```

### Batch Test Multiple Projects

```bash
cat > batch_test.py << 'EOF'
import sys
sys.path.insert(0, '/home/mk/cpp_tools')

from cpplib import CPPCodebase
from pathlib import Path

projects = [
    "/tmp/real_cpp_project",
    "/home/mk/program/gen",
]

for project_path in projects:
    if Path(project_path).exists():
        try:
            print(f"\nTesting: {project_path}")
            codebase = CPPCodebase(project_path)
            funcs = codebase.get_all_functions()
            classes = codebase.get_all_classes()
            print(f"  ✓ {len(funcs)} functions, {len(classes)} classes")
        except Exception as e:
            print(f"  ✗ Error: {e}")
EOF

uv run python batch_test.py
```

## Advanced uv Usage

### Create Project-Specific pyproject.toml

```toml
[project]
name = "cpplib"
version = "0.1.0"
description = "C++ Code Analysis and Manipulation Library"
requires-python = ">=3.9"
dependencies = [
    "tree-sitter>=0.26.0",
    "tree-sitter-cpp>=0.23.4",
    "pydantic>=2.5.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.3",
    "pytest-cov>=4.1.0",
    "black>=23.12.0",
    "flake8>=6.1.0",
    "mypy>=1.7.1",
]

[tool.black]
line-length = 100

[tool.mypy]
python_version = "3.9"
check_untyped_defs = true
```

### Lock Dependencies

```bash
# Create lock file with uv
uv pip compile pyproject.toml -o requirements-lock.txt

# Use lock file in CI/CD
uv pip sync requirements-lock.txt
```

### Run Tests in Different Python Versions

```bash
# Test with Python 3.9
uv venv --python 3.9
source .venv/bin/activate
uv pip install -e ".[dev]"
uv run pytest tests/

# Test with Python 3.11
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
uv run pytest tests/
```

## Performance: uv vs pip

### Speed Comparison

```bash
# Time with pip (old)
time pip install -e ".[dev]"

# Time with uv (new)
time uv pip install -e ".[dev]"
```

**Expected results:**
- uv is typically **5-10x faster** than pip
- Dependency resolution is near-instant
- Installation is parallelized

## Troubleshooting

### Issue: "Python not found"

```bash
# Specify Python path
uv venv --python /usr/bin/python3.11

# Or use pyenv
uv venv --python ~/.pyenv/versions/3.11.0/bin/python
```

### Issue: "Package not found"

```bash
# Upgrade uv
uv --version

# Clear cache
rm -rf ~/.cache/uv/

# Try again
uv pip install package-name
```

### Issue: Import errors in tests

```bash
# Ensure cpplib is in path
export PYTHONPATH="/home/mk/cpp_tools:$PYTHONPATH"

uv run pytest tests/
```

## Complete Testing Checklist with uv

```bash
#!/bin/bash
set -e

echo "🔧 Setting up with uv..."
uv venv
source .venv/bin/activate

echo "📦 Installing dependencies..."
uv pip install -e ".[dev]"

echo "🧪 Running tests..."
uv run pytest tests/ -v --cov=cpplib

echo "🎨 Formatting code..."
uv run black cpplib/

echo "🔍 Linting..."
uv run flake8 cpplib/

echo "📝 Type checking..."
uv run mypy cpplib/

echo "✅ All checks passed!"
```

Save as `test_all.sh` and run:
```bash
chmod +x test_all.sh
./test_all.sh
```

## CI/CD Integration with uv

### GitHub Actions Example

```yaml
name: Tests with uv

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.9", "3.10", "3.11"]

    steps:
      - uses: actions/checkout@v3
      
      - name: Install uv
        uses: astral-sh/setup-uv@v1
      
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: ${{ matrix.python-version }}
      
      - name: Install dependencies
        run: uv pip install -e ".[dev]"
      
      - name: Run tests
        run: uv run pytest tests/ -v
      
      - name: Lint
        run: uv run flake8 cpplib/
      
      - name: Type check
        run: uv run mypy cpplib/
```

---

## Benefits of Using uv

✅ **Speed** - 10-100x faster than pip  
✅ **Reliability** - Better dependency resolution  
✅ **Simplicity** - Single tool for everything  
✅ **Modern** - Built in Rust, actively maintained  
✅ **Compatible** - Works with existing pip workflows  

Start using uv today for faster, more reliable Python development! 🚀
