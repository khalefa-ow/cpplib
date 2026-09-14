# cpplib - C++ Code Generation & Manipulation Library

A Python library for reading, analyzing, and modifying C++ code. Supports semantic understanding of functions, classes, and files.

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
from cpplib import CPPCodebase

# Load a C++ project
codebase = CPPCodebase("/path/to/project")

# Extract a function with dependencies
func_piece = codebase.extract_function("MyClass::myMethod")
print(func_piece.dependencies)

# Add a new function
from cpplib import CodePiece
new_func = CodePiece(
    name="newFunction",
    kind="function",
    signature="void newFunction(int x)",
    body="{ /* implementation */ }",
)
codebase.insert_function("file.cpp", new_func, after="someFunction")

# Validate changes compile
codebase.validate()
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
