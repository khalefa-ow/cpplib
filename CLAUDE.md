# cpplib - C++ Code Generation & Manipulation Library

## Project Vision

A Python library that reads, analyzes, and modifies C++ code with semantic understanding. Goes beyond simple parsing to enable:
- Extracting functions/classes with their dependencies
- Adding new functions/classes to existing files
- Understanding code structure and generating modifications
- Validating changes compile correctly

## Design Goals

1. **Semantic parsing**: Use tree-sitter C++ parser + semantic layer (not regex/naive parsing)
2. **Generation & optimization**: Primary use case is code generation and refactoring
3. **Compilation-based validation**: Use cmake to verify all changes compile
4. **Dependency tracking**: Extract and understand dependencies within the codebase only (not external libraries)
5. **Composable code units**: Functions/classes + dependencies as extractable "pieces"

## Architecture

```
cpplib/
├── parser/              # Tree-sitter C++ → AST
├── semantic/            # Type system, scope tracking, dependency graph
├── pieces/              # Code extraction/composition (CodePiece abstraction)
├── generator/           # Code generation & modification
├── validator/           # CMake-based compilation verification
├── diff/                # Patch-based diffing
├── codebase.py          # Main CPPCodebase class
└── __init__.py          # Public API
```

## Key Classes

- **CPPCodebase**: Main entry point - manages a C++ project directory
- **CodePiece**: Represents an extractable code unit (function/class) with dependencies

## Development Phases

### Phase 1: Project Setup (DONE)
- [x] Initialize git repository
- [x] Create Python package structure
- [x] Set up dependencies (tree-sitter, pydantic, pytest)
- [x] Create base classes (CPPCodebase, CodePiece)
- [x] Create CMakeLists.txt for test validation

### Phase 2: Parser Layer (DONE)
- [x] Implement tree-sitter C++ parser wrapper (CPPParser)
- [x] Extract AST nodes (functions via FunctionExtractor)
- [x] Build file index (FileIndex for codebase scanning)
- [x] Extract function metadata (name, signature, body, line numbers)
- [x] Test suite: 10 tests, all passing

### Phase 3: Semantic Layer (DONE)
- [x] Build scope resolver (ScopeResolver, Symbol class)
- [x] Implement dependency graph (includes, types, calls, transitive resolution)
- [x] Type tracking and dependency queries
- [x] Full codebase analysis on CPPCodebase init
- [x] Test suite: 15 tests, all passing

### Phase 4: Pieces & Extraction (DONE)
- [x] Implement function/class extraction (ClassExtractor)
- [x] Dependency collection (DependencyCollector, includes, forward decls, type deps)
- [x] Extract methods: extract_function(), extract_class() on CPPCodebase
- [x] Test suite: 11 tests, all passing

### Phase 5: Generation & Modification
- [ ] Code generation from CodePiece
- [ ] AST-based insertion (before/after targets)
- [ ] Diff-based patching

### Phase 6: Validation
- [ ] CMake integration
- [ ] Build and validation
- [ ] Error reporting

## Quick Commands

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Format code
black cpplib/

# Type check
mypy cpplib/

# Lint
flake8 cpplib/
```

## Notes

- All paths in the codebase use `pathlib.Path` for cross-platform compatibility
- Type hints are required (checked with mypy)
- Tests go in `tests/` directory, fixtures in `tests/fixtures/`
- Use pydantic for data validation where needed
