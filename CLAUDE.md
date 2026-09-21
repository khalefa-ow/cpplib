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

A staged LLM pipeline built on top of cpplib for generating and optimizing query engines:
**storage plan → divide into hint levels → generate storage-layout header → generate per-query C++ and fix until it compiles and matches DuckDB gold → optimize with hints**.

### Directory Structure

```
agent/
├── cli/                    # Command-line interface
│   ├── __init__.py
│   ├── __main__.py        # Entry point (doctor, run, prompts)
│   ├── doctor.py          # Dependency checker (deno, cmake, g++, API keys)
│   ├── runner.py          # Main pipeline runner
│   └── prompts.py         # Prompt registry builder
│
├── stages/                 # Pipeline stage implementations
│   ├── storage_plan.py     # Stage 1: Parse database schema → storage layout plan
│   ├── divide.py           # Stage 2: Plan → separate per-query generation tasks + hint levels
│   ├── hppgen.py           # Stage 3: Generate C++ storage-layout header
│   ├── query_codegen.py    # Stage 4: Generate per-query C++ implementation with compile/correctness loop
│   ├── optimize.py         # Stage 5: Apply query hints to optimize performance
│   ├── results.py          # Artifact tracking (input_fingerprint, output, metrics)
│   └── base.py             # Stage base class (StageConfig, StageResult)
│
├── rlm/                    # ReAct-like Model (dspy.RLM) integration
│   ├── workspace.py        # CPPCodebase wrapper + file I/O; contains workarounds for known cpplib defects
│   ├── tools.py            # Tool definitions passed to dspy.RLM (read, write, compile, test, check_syntax)
│   └── __init__.py
│
├── llm/                    # LLM configuration and caching
│   ├── lm_factory.py       # configure_dspy() — centralized dspy config (model, temperature, max_tokens, cache)
│   ├── cache.py            # Request/response logging and caching layer
│   └── __init__.py
│
├── prompting/              # Prompt management
│   ├── manifest.json       # Registry of prompts (path → content_hash for invalidation)
│   ├── loader.py           # Load and validate prompts from manifest
│   ├── validator.py        # Validate prompt syntax and interpolations
│   └── __init__.py
│
├── examples/               # Example configurations and datasets
│   ├── config.example.json # Sample pipeline run config (schema, queries, hint levels, etc.)
│   ├── convert_dataset.py  # Convert TPC-DS CSV → Parquet for faster iteration
│   └── dataset/            # Sample datasets (sf=0.25 scale factor)
│       └── sf0.25/
│           ├── customer.csv, orders.csv, region.csv
│           └── *.parquet    # Parquet versions for faster loading
│
├── __init__.py
└── README.md               # Detailed agent/ workflow documentation

../prompts/                 # Shared prompt files (indexed by agent/prompting/manifest.json)
├── storage_plan_task.txt
├── divide_task.txt
├── hppgen_task.txt
├── query_codegen_task.txt
├── fix_compile_errors.txt
├── optimize_task.txt
└── *.txt                   # Additional prompts referenced in manifest.json
```

### How It Works

The pipeline is orchestrated by `agent/cli/runner.py` and driven by **dspy.RLM** (ReAct-like Model), not by hardcoded logic:

1. **storage_plan**: LLM reads database schema (Parquet), outputs a storage layout plan (which columns in which structure)
2. **divide**: LLM receives the plan + queries, breaks it into per-query hint-level tasks (hint level 0, 1, 2, ...)
3. **hppgen**: LLM generates C++ storage-layout header (struct definitions, buffer management) — no compile loop
4. **query_codegen**: LLM generates per-query C++ implementation. **Compile/correctness loop**: 
   - Compile the generated C++
   - If it fails, show compile errors and re-prompt the LLM to fix them
   - If it compiles, run golden validation (compare output vs. DuckDB)
   - If validation fails, re-prompt with the mismatch details
   - Continue for up to `params.max_rounds` retries
5. **optimize**: LLM applies performance-tuning hints to the generated engine (e.g., "use vectorized filters", "add index on key column")

Each stage produces an **Artifact** (stored in `params.artifacts_dir/`):
```python
{
  "stage": "query_codegen",
  "query_id": "q1",
  "hint_level": 0,
  "status": "verified",  # or "unverified", "failed"
  "input_fingerprint": "sha256(...)",  # Hash of input (query, schema, prior artifacts)
  "rounds": 3,
  "output": "... generated C++ code ...",
  "metrics": { "compile_time": 0.42, "golden_time": 1.2 }
}
```

**Key invariant**: Only stages with `input_fingerprint` (succeeded runs) are current. If you re-run with the same inputs, the pipeline skips that stage's LLM call and uses the cached artifact.

### Configuration

Run via: `python -m agent.cli run --config <path-to-json> [--dry-run]`

Config JSON structure:
```json
{
  "schema_path": "path/to/dataset.parquet",
  "queries_path": "path/to/queries.json",
  "hint_levels": [0, 1, 2],
  "artifacts_dir": "./artifacts",
  "gen_project_root": "./gen",
  "run_command": "cmake && make test",
  "max_rounds": 5
}
```

### Commands

```bash
uv pip install -e ".[dev,agent]"   # dspy, duckdb, pyarrow
python -m agent.cli doctor         # Check Deno, cmake, g++, API keys
python -m agent.cli run --config agent/examples/config.example.json --dry-run
python -m agent.cli prompts set <id> --file draft.txt   # Add/edit one prompt's text
python -m agent.cli prompts build                        # Resync placeholders/version
pytest tests/agent -v              # 352 tests, no API key needed
```

### Important Notes

- **Dependency isolation**: `agent/` is optional. cpplib itself only depends on tree-sitter + pydantic. Nothing in `cpplib/` imports from `agent/`.
- **LLM configuration**: All dspy calls go through `agent/llm/lm_factory.py`. Do NOT call `dspy.configure()` elsewhere.
- **Deno runtime**: `dspy.RLM` requires Deno. `agent.cli doctor` checks for it.
- **Prompts**: Stored inline in `agent/prompting/manifest.json` (a `text` field per entry) — there are no separate `prompts/*.txt` files and no stored content hash. Edit a prompt via `python -m agent.cli prompts set <id> --file <path>` (or `--text`), or hand-edit the entry's `text` field directly. `PromptEntry.fingerprint()` hashes `text` directly at use time, so an edit invalidates the right cache entries immediately, with no rebuild required for that. Run `python -m agent.cli prompts build` afterward anyway to resync the informational `placeholders` list and validate `$`-escaping. `agent.prompting.import_prompts_dir()` remains for bulk-importing an external directory of `.txt` files.
- **Workspace workarounds**: `agent/rlm/workspace.py` contains workarounds for cpplib defects:
  - `FileModifier` can clobber concurrent edits
  - No index invalidation after file changes
  - Text-based function lookup (fragile)
  - `check_syntax()` ignores `-std=` flag
  
  These have regression tests; fix the underlying cpplib bugs before removing workarounds.
- **Correctness validation**: `query_codegen` and `optimize` require `params.run_command` to execute the generated engine and verify correctness. Without it, `query_codegen` reports `unverified` and `optimize` refuses to start.
- **One hint level per run**: `query_codegen` generates **one hint level per run**. To compare levels, run the pipeline once per level with separate `artifacts_dir` and `gen_project_root`.
- **Driven by stages**: Compile/correctness loops are driven by stage logic, not by the LLM. Round budgets are enforced; round counts in metrics are measured, not self-reported.
- **API key isolation**: `tests/agent/conftest.py` deletes every provider API key before tests. Keep it that way — the test suite must never make billable calls.

## Quick Commands

```bash
# Install development dependencies
uv pip install -e ".[dev]"

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

- **Claude should not edit C++ files directly**. Work through the Python code. C++ changes should only be generated via:
  - `agent/stages/hppgen.py` (LLM-driven header generation)
  - `agent/stages/query_codegen.py` (LLM-driven query codegen with compile loop)
  - `cpplib/generator/` (programmatic C++ generation via CodeGenerator)
  
  If you encounter test C++ files that need fixing, fix the Python tests or the Python generator instead. The C++ in `tests/fixtures/` is test data, not source code.

- All paths in the codebase use `pathlib.Path` for cross-platform compatibility
- Type hints are required (checked with mypy)
- Tests go in `tests/` directory, fixtures in `tests/fixtures/`
- Use pydantic for data validation where needed
