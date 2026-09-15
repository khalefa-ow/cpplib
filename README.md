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
pip install -e ".[dev]"
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
