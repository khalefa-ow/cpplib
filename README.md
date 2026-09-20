# cpplib - C++ Code Generation & Manipulation Library

A Python library for reading, analyzing, and modifying C++ code with semantic understanding. Extract functions/classes with their dependencies, insert them into new locations, and validate changes compile correctly.

## Features

- **Semantic parsing**: Real C++ parser with type and scope awareness
- **Code extraction**: Extract functions/classes with their dependencies
- **Code generation**: Add functions, classes, and modify existing code
- **Dependency tracking**: Understand intra-codebase dependencies
- **Compilation validation**: Verify changes compile with cmake

## Architecture

```
cpplib/
├── parser/          # Tree-sitter C++ → AST
├── semantic/        # Type system, scope tracking, dependency graph
├── pieces/          # Code extraction/composition
├── generator/       # Code generation & modification
├── validator/       # CMake-based verification
└── diff/            # Patch-based diffing
```

## Installation

```bash
uv pip install -e ".[dev]"
```

## Quick Start

```python
from cpplib import CPPCodebase, CodePiece

# Load a C++ project
codebase = CPPCodebase("/path/to/project")

# Extract a function with all dependencies
func = codebase.extract_function("add")
print(f"Function: {func.name}")
print(f"Signature: {func.signature}")
print(f"Dependencies: {func.dependencies}")

# Extract a class with members
cls = codebase.extract_class("Calculator")

# Get all functions and classes in the codebase
functions = codebase.get_all_functions()
classes = codebase.get_all_classes()

# Create a new function
new_func = CodePiece(
    name="multiply",
    kind="function",
    signature="int multiply(int a, int b)",
    body="{ return a * b; }",
)

# Insert function into a file at a specific location
codebase.insert_function("src/math.cpp", new_func, after="add")

# Apply modifications
success = codebase.generate()

# Validate changes compile
is_valid, message = codebase.validate()
print(f"Compilation: {message}")
```

## Documentation

**📖 Complete Documentation:**
- **[docs/USAGE.md](docs/USAGE.md)** - Comprehensive usage guide with 10+ examples
- **[docs/README.md](docs/README.md)** - Documentation index and overview

**📚 Feature Guides:**
- **[docs/REPORTER_GUIDE.md](docs/REPORTER_GUIDE.md)** - Text output and formatting
- **[docs/CPP_STANDARDS_GUIDE.md](docs/CPP_STANDARDS_GUIDE.md)** - C++20/C++23 support
- **[docs/TESTING_FROM_ANYWHERE.md](docs/TESTING_FROM_ANYWHERE.md)** - Testing guide

**🎬 Examples:**
- **[examples/README.md](examples/README.md)** - Example overview
- **[examples/demo.py](examples/demo.py)** - Basic feature showcase
- **[examples/demo_reporter.py](examples/demo_reporter.py)** - Text output examples
- **[examples/demo_validation.py](examples/demo_validation.py)** - Compilation validation
- **[examples/demo_cpp20_features.py](examples/demo_cpp20_features.py)** - C++20/C++23 features

**📋 Project Info:**
- **[CLAUDE.md](CLAUDE.md)** - Architecture and development phases

## Run Examples

```bash
# Basic feature showcase
python examples/demo.py

# Text output and formatting
python examples/demo_reporter.py

# Compilation validation and error handling
python examples/demo_validation.py

# C++20/C++23 features
python examples/demo_cpp20_features.py
```

## Development

Run tests:
```bash
pytest tests/ -v
```

Format code:
```bash
black cpplib/
```

Type check:
```bash
mypy cpplib/
```
