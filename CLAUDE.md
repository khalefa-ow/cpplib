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

### Phase 5: Generation & Modification (DONE)
- [x] Code generation from CodePiece (CodeGenerator)
- [x] File insertion (before/after targets via FileModifier)
- [x] Replace and delete functions
- [x] Track and apply modifications
- [x] Test suite: 17 tests, all passing

### Phase 6: Validation (DONE)
- [x] CMake integration (CMakeValidator)
- [x] Build and validation (cmake + g++/clang++)
- [x] Syntax checking without full compilation
- [x] Error reporting and output tracking
- [x] Test suite: 8 tests, all passing

## Project Complete! ✅

All 6 phases implemented and tested. cpplib is now a fully functional C++ code analysis and modification library with 70 passing tests.

## `agent/` — DSPy workflow layer (separate package)

A staged LLM pipeline built on top of cpplib: storage plan → divide into hint
levels → generate storage-layout header → generate per-query C++ and fix until
it compiles and matches DuckDB gold → optimize with hints. See
[agent/README.md](agent/README.md).

Status: complete. All five stages (`storage_plan`, `divide`, `hppgen`,
`query_codegen`, `optimize`) are implemented and tested offline.

```bash
uv pip install -e ".[dev,agent]"   # dspy, duckdb, pyarrow
python -m agent.cli doctor         # deno (required by dspy.RLM), cmake, g++, API keys
python -m agent.cli run --config agent/examples/config.example.json --dry-run
pytest tests/agent -v              # 352 tests, no API key needed
```

Notes for future work:
- `agent/` is optional and dependency-isolated: cpplib itself stays tree-sitter
  + pydantic only. Nothing in `cpplib/` imports from `agent/`.
- `dspy.RLM` needs the **Deno** runtime; `agent.cli doctor` checks for it.
- All LLM access goes through `agent/llm/lm_factory.py` (`configure_dspy`) and is
  cached by `agent/llm/cache.py`. Do not call `dspy.configure` elsewhere.
- `agent/rlm/workspace.py` documents and works around several cpplib defects
  (`FileModifier` edit clobbering, no index invalidation, text-based function
  lookup, `check_syntax` ignoring `-std=` and returning `None`). Those
  workarounds have regression tests; fix the underlying cpplib bugs before
  removing them.
- Prompts live in `prompts/*.txt`, indexed by `agent/prompting/manifest.json`.
  Run `python -m agent.cli prompts build` after editing any prompt, or the
  registry will refuse to use a stale entry.
- `query_codegen` and `optimize` need `params.run_command` to execute the
  generated engine; without it `query_codegen` reports every query as
  `unverified` (it never claims correctness it did not check) and `optimize`
  refuses to start.
- `query_codegen` generates **one hint level per run**. To compare levels, run
  the pipeline once per level with its own `artifacts_dir` and
  `gen_project_root`.
- The compile/correctness loops are driven by the stages, not by the model, so
  the round budgets are enforced and the round counts in `metrics` are measured
  rather than self-reported. `params.rlm_tools` hands the model the workspace
  tools instead.
- An artifact carries `input_fingerprint` only when the stage succeeded. That
  field is what marks an artifact current, so stamping a failed run would make
  the pipeline skip the retry.
- `tests/agent/conftest.py` deletes every provider API key for every test. Keep
  it that way: the suite must not be able to make a billable call.

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
