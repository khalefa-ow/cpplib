# Agent: DSPy-Driven C++ Query Engine Generation

## Overview

`agent/` is a staged LLM pipeline built on top of cpplib that generates and optimizes in-memory C++ query engines using **dspy.RLM** (ReAct-like Model).

**The workflow**: storage plan → divide into hint levels → generate storage-layout header → generate per-query C++ (with compile/correctness loop) → optimize with performance hints.

## Pipeline Stages

### Stage 1: `storage_plan`
**Input**: Database schema (Parquet) + optional domain knowledge  
**Output**: Storage layout plan (which columns in which data structure)  
**Implementation**: `agent/stages/storage_plan.py`

Uses dspy.RLM to analyze the schema and produce a structured storage plan that guides the remaining stages.

### Stage 2: `divide`
**Input**: Storage plan + queries to implement  
**Output**: Per-query hint-level tasks (organized by optimization level)  
**Implementation**: `agent/stages/divide.py`

Breaks the problem into tractable per-query generation tasks, separating by hint level:
- Level 0: No hints (schema facts only)
- Level 1: Organization-level hints (structured, clear groupings)
- Level 2+: Full hints (all optimization advice)

### Stage 3: `hppgen`
**Input**: Storage plan + hint levels  
**Output**: C++ storage-layout header (struct definitions, buffers, access methods)  
**Implementation**: `agent/stages/hppgen.py`

Generates the C++ storage abstraction without a compile loop (one generation pass). Output: `storage_layout_*.hpp`

### Stage 4: `query_codegen`
**Input**: Per-query task + storage-layout header + dataset  
**Output**: Per-query C++ implementation with compile/correctness verification  
**Implementation**: `agent/stages/query_codegen.py`

**Compile/correctness loop**:
1. LLM generates C++ implementation
2. Try to compile with g++/clang++
3. If compile fails: show errors, re-prompt LLM to fix → retry (up to `max_rounds`)
4. If compiles: run golden validation (compare output vs. DuckDB reference)
5. If validation fails: show mismatch, re-prompt LLM to fix → retry
6. Mark artifact as `verified` when both compile and validation pass

### Stage 5: `optimize`
**Input**: Verified query implementations + performance hints  
**Output**: Optimized implementations (vectorized filters, indexes, prefetching, etc.)  
**Implementation**: `agent/stages/optimize.py`

LLM applies performance-tuning hints to the generated code, respecting the same compile/correctness loop.

## Architecture

### Core Components

- **CLI** (`agent/cli/`): Command-line interface
  - `doctor`: Check dependencies (Deno, cmake, g++, API keys)
  - `run`: Execute the pipeline
  - `prompts`: Rebuild prompt registry after editing

- **Stages** (`agent/stages/`): Pipeline stage implementations
  - Base class (`base.py`) defines stage interface
  - Results tracking (`results.py`): Artifacts with fingerprinting for caching
  - Each stage is independent; runs only if inputs change

- **RLM Integration** (`agent/rlm/`): dspy.RLM wrapper
  - `workspace.py`: CPPCodebase wrapper + file I/O operations
  - `tools.py`: Tool definitions for the LLM (read, write, compile, test, check_syntax)
  - Contains workarounds for known cpplib defects with regression tests

- **LLM Config** (`agent/llm/`): Centralized LLM setup
  - `lm_factory.py`: `configure_dspy()` — single point for model config
  - `cache.py`: Request/response caching layer

- **Prompting** (`agent/prompting/`): Prompt management
  - `manifest.json`: Registry of prompts + content hashes (invalidation)
  - `loader.py`: Load prompts from registry
  - `validator.py`: Validate prompt syntax

- **Examples** (`agent/examples/`): Configurations and sample datasets
  - `config.example.json`: Full pipeline configuration
  - `convert_dataset.py`: Convert CSV → Parquet (TPC-DS)
  - `dataset/sf0.25/`: Sample TPC-DS at scale 0.25

### Artifact Storage

Each stage produces one or more **Artifact** JSON files under `artifacts_dir/<stage>/`. A `query_codegen` artifact, for example, carries: the hint `level` it was generated for, `status` (`verified`/`unverified`/`failed`), an `input_fingerprint` (only set once the stage actually succeeded), round counts, the generated output, and timing metrics.

**Key invariant**: Only artifacts with `input_fingerprint` are current (stage succeeded). Re-running with the same inputs skips the LLM call and reuses the artifact.

## Configuration

Run via: `python -m agent.cli run --config <path-to-json> [--dry-run]`

Configs use two top-level sections — see `agent/examples/config.example.json` for the full, real shape:
- `common`: shared settings (inputs, `gen_project_root`, `levels`, `model`, `cache`, `trace`, `compile`, `gold`)
- `stages`: per-stage overrides (`storage_plan`, `divide`, `hppgen`, `query_codegen`, `optimize`), each with its own `active_levels`, `params`, `prompt_ids` and `cache`

Key `common` settings:
- `inputs.schema` / `inputs.queries`: the database schema and workload to generate against
- `gen_project_root`: where generated C++ files are written
- `levels`: the hint levels declared for the whole pipeline (name, namespace, output file)
- `gold`: where reference results come from (DuckDB, an external command, or pre-existing files)
- `compile.max_fix_rounds`: compile-repair budget per query/header

Per-stage `params.run_command` (on `query_codegen`/`optimize`) is what actually executes the generated engine; without it, correctness is never claimed. See [agent/README.md](agent/README.md) for the full reference.

## Multi-Level Code Generation

**`query_codegen` (and `hppgen`) support generating multiple hint levels in one call.**

Set more than one name in `active_levels` for both stages, and each processes every configured level sequentially within its own single stage run — no extra flag needed:

```json
"hppgen": { "active_levels": ["no_hints", "organization_level", "all_hints"] },
"query_codegen": { "active_levels": ["no_hints", "organization_level", "all_hints"] }
```

Both `hppgen.active_levels` and `query_codegen.active_levels` must list the same levels — `query_codegen` needs a generated header for every level it targets, so a mismatch fails with "No generated header for level '…'. Run hppgen for this level first."

**For comparing levels**: Each level's query sources are written to its own subdirectory (avoiding cross-level clobbering), and each level's `query_sources`/`correctness_report` artifacts are written separately. The stage's `metrics` report an aggregate `verified`/`unverified` across all levels plus a per-level `levels_verified` count.

## Step-by-Step Runs

`--steps N` runs N **pipeline stages** one at a time, advancing automatically without needing to invoke the CLI once per stage. The default pipeline has exactly 5 stages, so `--steps 5` runs the whole thing stage by stage:

```bash
python -m agent.cli run --config agent/examples/config.example.json --steps 5
```

Progress prints after every step (`[step 2/5] hppgen`, then its report), so a long run can be watched without waiting for the whole pipeline to finish. A step that covers several hint levels (`hppgen`, `query_codegen`) still generates every active level within that one step — `--steps` counts stages, not levels. By default a failing step halts the run; pass `--keep-going` to run the remaining steps anyway.

## Quick Start

```bash
# Install with agent dependencies
uv pip install -e ".[dev,agent]"

# Check environment (Deno, cmake, g++, API keys)
python -m agent.cli doctor

# Run the full pipeline in one shot
python -m agent.cli run --config agent/examples/config.example.json

# Run it stage by stage, watching progress after each
python -m agent.cli run --config agent/examples/config.example.json --steps 5

# Run in dry-run mode (print plan, don't execute)
python -m agent.cli run --config agent/examples/config.example.json --dry-run

# (optional) Resync a prompt's placeholders after editing its text
python -m agent.cli prompts build

# Run tests (no API key needed)
pytest tests/agent -v
```

## Key Design Decisions

### 1. dspy.RLM for Workflow Control
- **Why**: ReAct pattern naturally models compile-fix loops and correctness validation
- **Tool integration**: LLM uses workspace tools (read, write, compile, test, check_syntax) for each task
- **Not scripted**: The stage doesn't hardcode retry logic; the LLM does via its action loop

### 2. Artifact Fingerprinting
- **Why**: Enables deterministic caching and re-runs without re-generating
- **Mechanism**: `input_fingerprint` is a hash of the input (query, schema, prior artifacts)
- **Invariant**: Only stages with `input_fingerprint` are marked as current

### 3. Compile/Correctness Loop in query_codegen
- **Why**: Catch errors early and let the LLM fix them, not humans
- **Rounds**: Enforced budget (e.g., 5 retries) to prevent infinite loops
- **Metrics**: Round counts are measured, not self-reported

### 4. Dependency Isolation
- **Why**: cpplib stays lightweight (tree-sitter + pydantic only)
- **Implementation**: `agent/` is optional; nothing in `cpplib/` imports from `agent/`
- **Benefit**: cpplib can be used standalone, or with agent for the full pipeline

### 5. Centralized LLM Config
- **Why**: Single point to change models, temperatures, cache settings
- **Implementation**: `agent/llm/lm_factory.py` (`configure_dspy()`)
- **Never**: Call `dspy.configure()` elsewhere

### 6. Multi-Level Support & Step-by-Step Runs
- **Why**: Compare implementations across hint levels (no hints, intermediate, full hints), and watch a long pipeline run progress without waiting for it to finish
- **How**: Set `stages.query_codegen.active_levels` (and matching `stages.hppgen.active_levels`) to multiple level names — both stages then generate every level in one call; separately, `--steps N` runs N pipeline *stages* one at a time, advancing automatically
- **Benefit**: Generate level comparisons unattended, each producing separate tagged artifacts; watch or resume a run stage by stage
- **Implementation**: `query_codegen`/`hppgen` loop through all `active_levels` internally; `agent/cli.py`'s `_run_step_by_step()` drives the stage-at-a-time loop

## Known Workarounds (Regression Tested)

These are bugs in cpplib that `agent/rlm/workspace.py` works around. Fix the underlying cpplib defects before removing:

- `FileModifier` can clobber concurrent edits
- No index invalidation after file modifications
- Text-based function lookup (fragile to whitespace changes)
- `check_syntax()` ignores `-std=` compiler flag and may return `None`

See `agent/rlm/workspace.py` for details and regression tests.

## Prompts

Prompt text lives **inline** in `agent/prompting/manifest.json` — each entry has a `text` field holding the full template, alongside its metadata (`stage`, `role`, `placeholders`, `composes`, `version`, `description`). There are no separate `prompts/*.txt` files and no stored content hash; the manifest is the only source of truth.

**Add or edit one prompt**:
```bash
# From a local file (compose it in an editor, then embed it)
python -m agent.cli prompts set query_codegen_task --file draft.txt

# Directly on the command line
python -m agent.cli prompts set my_prompt --text "Summarize \${topic}."
```

**After a hand edit to an entry's `text` field**, or after `prompts set`, resync the derived fields:
```bash
python -m agent.cli prompts build
```

This recomputes `placeholders` for every entry from its own `text` and validates `$`-escaping. It does **not** need to run for cache invalidation to work: `PromptEntry.fingerprint()` hashes `text` directly at use time, so an edit is picked up the moment it's saved, whether or not a rebuild followed.

**Bulk-importing an external directory** of `.txt` files (e.g. migrating prompts in from elsewhere) is still supported via `--prompts-dir`:
```bash
python -m agent.cli prompts build --prompts-dir some/external/prompts/
```
This merges the directory's files in as inline entries; ids outside the batch are left untouched.

**Inspect prompts**:
```bash
python -m agent.cli prompts list                       # id, stage/role, first line
python -m agent.cli prompts show query_codegen_task     # rendered
python -m agent.cli prompts show query_codegen_task --raw   # unrendered template
```

## References

- **Full agent documentation**: [agent/README.md](agent/README.md)
- **CLAUDE.md agent section**: [CLAUDE.md](CLAUDE.md#agent---dspy-workflow-layer-separate-package)
- **Stage implementations**: `agent/stages/`
- **Example config**: `agent/examples/config.example.json`
- **Test suite**: `tests/agent/` (352 tests)

## Legacy Config Reference

Three config shapes the loader (`agent/config/loader.py`) has had to normalize over the project's history, kept here as living regression fixtures (`tests/agent/test_agent_config.py` parses the JSON blocks straight out of this file and loads each one). Historical shapes are shown as-is, quirks included, to prove the normalizer still accepts what real runs actually used.

### Current shape (`common`/`stages`, matches `agent/examples/config.example.json`)

```json
{
  "common": {
    "base_dir": ".",
    "inputs": {
      "schema": "input/schema.txt",
      "queries": "input/allqueries.txt",
      "statistics": "input/statistics.txt"
    },
    "artifacts_dir": "out/artifacts",
    "gen_project_root": "./out/gen",
    "levels": [
      { "name": "no_hints", "namespace": "basic", "file": "storage_layout_basic.hpp" },
      { "name": "organization_level", "namespace": "intermediate", "file": "storage_layout_interm.hpp" },
      { "name": "all_hints", "namespace": "full", "file": "storage_layout_all.hpp" }
    ],
    "model": {
      "name": "deepseek/deepseek-coder",
      "temperature": 1.0,
      "max_tokens": 12000,
      "api_key_env": "DEEPSEEK_API_KEY",
      "track_usage": true,
      "lm_cache": true
    },
    "cache": {
      "enabled": true,
      "dir": "out/.cache",
      "refresh": false
    },
    "trace": {
      "path": "out/trace.jsonl",
      "stdout": false,
      "max_field_chars": 4000,
      "enable_weave": false,
      "enable_wandb": false
    },
    "rlm": {
      "max_iterations": 20,
      "max_llm_calls": 50,
      "max_output_chars": 100000,
      "threshold_chars": 100000,
      "verbose": false,
      "sub_model": {
        "name": "deepseek/deepseek-coder",
        "temperature": 1.0
      }
    },
    "compile": {
      "compiler": "g++",
      "cpp_standard": "c++20",
      "include_dirs": [],
      "extra_flags": ["-O2"],
      "cmake_dir": "out/gen",
      "build_dir": "out/gen/build",
      "timeout_s": 300,
      "max_fix_rounds": 2,
      "clean_build": false
    },
    "gold": {
      "mode": "duckdb",
      "duckdb_path": "./out/gold/gold.duckdb",
      "dir": "./out/gold",
      "extension": ".csv",
      "dataset_dir": "./dataset/sf0.25",
      "overwrite": false,
      "install_spatial": false
    }
  },
  "stages": {
    "storage_plan": {
      "enabled": true,
      "prompt_ids": ["storage_plan_policy"],
      "cache": { "dir": "out/.cache/storage_plan" }
    },
    "divide": {
      "enabled": true,
      "prompt_ids": ["divide_policy"],
      "cache": { "dir": "out/.cache/divide" },
      "outputs": { "schema_levels": "out/artifacts/schema_levels.json" }
    },
    "hppgen": {
      "enabled": true,
      "active_levels": ["no_hints", "organization_level", "all_hints"],
      "cache": { "dir": "out/.cache/hppgen" }
    },
    "query_codegen": {
      "enabled": true,
      "active_levels": ["no_hints", "organization_level", "all_hints"],
      "prompt_ids": ["query_codegen_task", "fix_compile_errors"],
      "cache": { "dir": "out/.cache/query_codegen" },
      "prompt_vars": {
        "compiler": "g++",
        "cpp_standard": "c++20",
        "command": "cmake --build out/gen/build",
        "diagnostics": "Use compiler error messages to fix issues"
      },
      "params": {
        "run_command": "build/engine --query {query_id} --out {output} --sf {sf} --dataset {dataset_dir}",
        "sf": "0.25",
        "source_dir": "src/queries",
        "build_project": "auto",
        "database_type_name": "Database",
        "database_param_name": "db",
        "query_namespace_prefix": "queries::",
        "query_function_name": "run",
        "header_mode": "gold",
        "sort_rows": false,
        "float_tolerance": 1e-6,
        "max_correctness_rounds": 5
      }
    },
    "optimize": {
      "enabled": true,
      "prompt_ids": ["optim_w_trace"],
      "cache": { "dir": "out/.cache/optimize" },
      "params": {
        "strategy": "trace",
        "max_rounds": 4,
        "min_improvement": 0.05,
        "repeat_runs": 3,
        "sf": "0.25",
        "target_factor": 2.0,
        "runtime_pattern": "total runtime: ([0-9.]+)s",
        "trace_flag": "--trace",
        "trace_log": "tracing_output.log"
      }
    }
  }
}
```

### Legacy shape: staged top-level keys (`common`/`divide`/`hppgen`/`query_codegen`)

```json
{
	"common": {
		"schema_path": "../input/schema.txt",
		"storage_plan_path": "../input/storage_plan.txt",
		"levels": [
			{
				"name": "no_hints",
				"namespace": "basic",
				"file": "storage_layout_basic.hpp"
			},
			{
				"name": "organization_level",
				"namespace": "intermidate",
				"file": "storage_layout_interm.hpp"
			},
			{
				"name": "all_hints",
				"namespace": "full",
				"file": "storage_layout_all.hpp"
			}
		]
	},
	"divide": {
		"policy": "Policy: split inputs into three levels.\n\nLevel 1 — no_hints:\n- Use only schema facts (tables, columns, types, keys, constraints, relationships).\n- Do NOT include storage-plan hints, notes, optimization or implementation advice.\n\nLevel 2 — organization_level:\n- Present organized schema facts using clear headings and groupings (Overview; Tables/Entities; Columns and Types; Keys and Constraints; Relationships).\n- Still do NOT include storage-plan hints or notes.\n\nLevel 3 — all_hints:\n- Include schema facts plus all hints and notes present in `storage_plan_txt`.\n- Do NOT invent new hints, storage decisions, indexes, layouts, encodings, or implementation details.\n- If something is only implied by the storage plan, mark it as \"Implied by storage plan\".\n\nFor each level, also produce a short one-sentence data structure description for downstream code generation.\n\nReturn valid JSON only in this shape, using the exact level names declared above: {\"levels\": {\"<level_name>\": \"<level text>\"}, \"data_structure_descriptions\": {\"<level_name>\": \"<short data structure description>\"}}.",
		"output": {
			"json_path": "../gen/schema_levels.json"
		},
		"llm": {
			"model": "openai/gpt-5.3-codex",
			"temperature": 1,
			"max_tokens": 12000
		},
		"cache": {
			"enabled": true,
			"dir": ".schema_level_cache"
		}
	},
	"hppgen": {
		"levels_path": "../gen/schema_levels.json",
		"out_dir": "../gen/",
		"root_class_name": "Database",
		"active_levels": [
			"no_hints"
		],
		"model": {
			"name": "openai/gpt-5.3-codex",
			"temperature": 1,
			"max_tokens": 12000
		},
		"trace_path": "output/storage_layout_trace.jsonl",
		"trace_stdout": true,
		"enable_wandb": true,
		"enable_weave": true,
		"use_cache": true,
		"refresh_cache": false,
		"cache_dir": ".cache/storage_layout_hpp"
	},
	"query_codegen": {
		"queries_path": "../input/allqueries.txt",
		"gold_dir": "gold",
		"gold_extension": ".csv",
		"dataset_dir": "../dataset/sf0.25",
		"gold_generate_with_duckdb": true,
		"gold_duckdb_path": "gold/gold.duckdb",
		"gold_overwrite": false,
		"gold_install_spatial": true,
		"out_dir": "../gen",
		"output_extension": ".cpp",
		"database_type_name": "Database",
		"database_param_name": "db",
		"query_namespace_prefix": "queries::",
		"result_prefix": "result_",
		"query_function_name": "run",
		"query_ids": [],
		"active_levels": [
			"no_hints"
		],
		"summary_lines": 20,
		"max_summary_chars": 12000,
		"trace_path": "gen3/query_codegen_trace.jsonl",
		"trace_stdout": true,
		"use_cache": true,
		"refresh_cache": false,
		"cache_dir": ".cache/query_codegen",
		"model": {
			"name": "openai/gpt-5.3-codex",
			"model_type": "chat",
			"temperature": 1,
			"max_tokens": null,
			"lm_cache": true,
			"num_retries": 3,
			"api_key_env": "OPENAI_API_KEY",
			"api_base": null,
			"adapter": null,
			"track_usage": true
		}
	}
}
```

### Legacy shape: flat single-stage config (pre-dates the `common`/`stages` split)

```json
{

    "levels_path": "schema_levels.json",
  "storage_layout_hpp": "generated/storage_layout_l.hpp",
  "model":     "openai/gpt-5.4-mini",
  "verify": true,
  "compile_check": true,
  "compiler": "g++",
  "cpp_standard": "c++20",
  "max_fix_rounds": 2,
  "fail_on_verify": true,

  "trace_path": "output/storage_layout_trace.jsonl",
  "trace_stdout": true,

  "use_cache": true,
  "refresh_cache": false,
  "cache_dir": ".cache/storage_layout_hpp",
  "no_llm_run":true,
  "gen_project_root": "/home/mk/gen4/",
  "base_dir": "/home/mk/v4/",
  "schema": "./input/schema.txt",
  "storage_plan": "./input/storage_plan.txt",
  "queries_file": "./input/allqueries.txt",
  "build_dir": "/home/mk/gen4/build",
  "actual_output_dir": "./output/",
  "task": "Generate a C++ program that runs each query and produces outputs matching the gold files. The implementation must follow the configured storage plan and schema.\nSpatial code generation specialization:\n- You are implementing an in-memory spatial query engine.\n- Work from the configured schema and storage plan as the source of truth.\n- Do not hard-code assumptions that contradict the schema or storage plan.\n- Use Arrow and Parquet APIs for ingestion.\n- Keep the project buildable after each patch.\n- Prefer simple, portable C++ and CMake for the spatial engine.\n- Create the following implementation files:\n 1. loader_impl.hpp\n 2. loader_impl.cpp\n 3. builder_impl.hpp\n 4. builder_impl.cpp\n- Split the implementation into clear components:\n 1. Loader: implemented in loader_impl.hpp and loader_impl.cpp; loads data from Parquet files using Arrow and Parquet APIs.\n 2. Builder: implemented in builder_impl.hpp and builder_impl.cpp; converts loaded Parquet data into the optimized in-memory layout defined by the storage plan.\n- The generated C++ project must remain buildable with CMake and include verification logic to confirm that queries run and outputs can be compared against the gold files. 5- main program should takes the following inputs: input dir, query_id, and query params. \n - Return only an apply_patch-compatible patch block. \n - Each patch must start with *** Begin Patch and end with *** End Patch. \n - Use only *** Add File:, *** Update File:, and *** Delete File: sections. \n - Do not use diff --git, ---, or +++ file headers.\n - Do not wrap the patch in markdown fences. \n -Use paths relative to the repository root."
  ,
  "gold_command": "uv run ./reference/run_query.py --query-text {query_text} --db_path  ./reference/sf_0.25_1.db --output {gold_output}",   
  "output_extension": ".csv",
  "input_dir": "./dataset/sf0.25/",
  "planner_models": [
    "openai/gpt-5.4-mini"
    
  ],
  "gold_output_dir": "./gold/",
  "patcher_models": [
    "openai/gpt-5.4-mini"
    
  ],
  "dataset_id":"sf_0.25",
  
  "enable_weave": true,
   "enable_wandb": true,
  
  "weave_project_name": "cpp-codegen-all-queries-workflow-2",
  "skip_gold_generation": false,
  "result_json": "./workflow_result.json"
}
```
