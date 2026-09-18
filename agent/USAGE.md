# Agent Usage Guide

A step-by-step guide to using the DSPy pipeline for C++ query engine generation, compilation, and optimization.

## Quick Start (5 minutes)

```bash
# 1. Install dependencies
uv pip install -e ".[dev,agent]"

# 2. Check your environment
python -m agent.cli doctor

# 3. Rebuild prompts (after editing any)
python -m agent.cli prompts build

# 4. Try a dry run (no API calls)
python -m agent.cli run --config agent/examples/config.example.json --dry-run

# 5. Run the full pipeline
python -m agent.cli run --config agent/examples/config.example.json
```

## Installation & Setup

### Prerequisites

- **Deno** (required by dspy.RLM for the Pyodide sandbox)
- **cmake** (for compiling generated C++ code)
- **g++** or **clang++** (C++ compiler, g++ by default)
- **Python 3.9+**
- **API key** for your LLM provider (OpenAI by default)

Run `python -m agent.cli doctor` to check everything:

```bash
$ python -m agent.cli doctor
✓ deno found at /usr/bin/deno
✓ cmake found at /usr/bin/cmake
✓ g++ found at /usr/bin/g++
✓ OPENAI_API_KEY is set
All checks passed!
```

If any check fails, install the missing tool or set the environment variable.

### Installing the Package

```bash
# Development install with agent dependencies
uv pip install -e ".[dev,agent]"

# Or with pip
pip install -e ".[dev,agent]"
```

This installs:
- `dspy` (LLM framework)
- `duckdb` (for gold results)
- `pyarrow` (data handling)
- Development dependencies (pytest, black, mypy, flake8)

## Configuration

### Creating Your Config

Copy the example and modify it:

```bash
cp agent/examples/config.example.json my-project.json
```

The config has two main sections:

#### `common` — Shared Settings

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
    "gen_project_root": "out/gen",
    "levels": [
      { "name": "no_hints", "namespace": "basic", "file": "storage_layout_basic.hpp" }
    ],
    "model": {
      "name": "openai/gpt-4",
      "api_key_env": "OPENAI_API_KEY",
      "max_tokens": 12000
    },
    "cache": {
      "enabled": true,
      "dir": "out/.cache"
    },
    "compile": {
      "compiler": "g++",
      "cpp_standard": "c++20",
      "build_dir": "out/gen/build"
    },
    "gold": {
      "mode": "duckdb",
      "duckdb_path": "out/gold/gold.duckdb",
      "dataset_dir": "dataset/sf0.25"
    }
  }
}
```

**Key fields:**

- **`base_dir`** — Root for relative paths (defaults to config file's directory)
- **`inputs`** — Paths to schema, queries, and optional statistics
- **`artifacts_dir`** — Where stage outputs go (plans, levels, headers, generated code)
- **`gen_project_root`** — Where C++ code is generated and built
- **`levels`** — Schema levels for ablation studies (no hints → organization hints → all hints)
- **`model`** — LLM config (see below)
- **`cache`** — Caching behavior
- **`compile`** — C++ compiler settings
- **`gold`** — How to get reference results

#### `stages` — Per-Stage Config

Override common settings for specific stages:

```json
{
  "stages": {
    "hppgen": {
      "active_levels": ["no_hints"],
      "cache": { "refresh": true }
    },
    "query_codegen": {
      "active_levels": ["no_hints"],
      "params": {
        "run_command": "./build/engine --query {query_id} --out {output} --sf {sf}",
        "max_correctness_rounds": 3
      }
    },
    "optimize": {
      "enabled": false
    }
  }
}
```

### Model Selection

Specify LLM models as `provider/model`:

```json
{
  "model": {
    "name": "openai/gpt-4",
    "api_key_env": "OPENAI_API_KEY"
  }
}
```

Supported providers: **OpenAI**, **DeepSeek**, and any LiteLLM backend.

The API key env var is filled from `api_key_env` or inferred from provider (`openai/` → `OPENAI_API_KEY`). If not found, the run fails with a clear error.

**Tip:** Use a faster, cheaper model for testing (e.g., `gpt-4-turbo`) and switch to your target model later.

## Running the Pipeline

### Basic Run

```bash
python -m agent.cli run --config my-project.json
```

This runs all enabled stages in dependency order (storage_plan → divide → hppgen → query_codegen → optimize).

### Common Options

```bash
# Dry run: check config and syntax, no API calls
python -m agent.cli run --config my-project.json --dry-run

# Force recompute all stages (still uses disk cache)
python -m agent.cli run --config my-project.json --force

# Run only specific stages
python -m agent.cli run --config my-project.json --stages divide,hppgen

# Start from a particular stage
python -m agent.cli run --config my-project.json --start-from query_codegen

# Disable a stage for this run
python -m agent.cli run --config my-project.json --no-optimize
```

### What Happens

1. **Resumption:** Stages skip if their outputs already match their inputs. Re-runs are cheap.
2. **Caching:** Model outputs are cached by prompt fingerprint + inputs. Edit a prompt → cache invalidates automatically.
3. **Compilation:** Generated C++ is compiled and fixed up to `max_fix_rounds` times.
4. **Verification:** Generated code is run against gold results; mismatches trigger re-generation.
5. **Optimization:** If all queries pass, optimization runs: propose a change → measure median of `repeat_runs` runs → keep if improvement ≥ `min_improvement`.

## Understanding Stages

### 1. `storage_plan` — Design the Storage Layout

Reads schema, queries, and statistics. Produces a plan for how to lay out data in memory.

```bash
python -m agent.cli run --config my-project.json --stages storage_plan
```

Output: `artifacts_dir/storage_plan.json` (the LLM's reasoning and final plan)

### 2. `divide` — Split into Levels

Splits the plan into multiple schema levels for ablation studies (e.g., no hints, basic hints, all hints).

Output: `artifacts_dir/schema_levels.json`

### 3. `hppgen` — Generate Storage Headers

Generates one C++ header per level (`storage_layout_basic.hpp`, `storage_layout_interm.hpp`, etc.), compiles it, and fixes errors.

```bash
python -m agent.cli run --config my-project.json --stages hppgen
```

**Only generates active levels** (set in `stages.hppgen.active_levels`):

```json
{
  "stages": {
    "hppgen": {
      "active_levels": ["no_hints", "organization_level"]
    }
  }
}
```

Output: Compiled headers in `gen_project_root/include/`

**Tip:** To compare multiple levels, run the full pipeline once per level with its own `artifacts_dir` and `gen_project_root`.

### 4. `query_codegen` — Generate & Verify Queries

Generates a `.cpp` file for each query, compiles it, and runs it against gold results.

Two separate budgets:
- **`compile.max_fix_rounds`** — Fix compilation errors (typically 1–2)
- **`params.max_correctness_rounds`** — Fix wrong results (typically 2–5)

The generated engine command is configured in `params.run_command`:

```json
{
  "query_codegen": {
    "params": {
      "run_command": "./build/engine --query {query_id} --out {output} --sf {sf}",
      "sf": "0.25"
    }
  }
}
```

Placeholders:
- `{query_id}` — Unique query identifier
- `{query_text}` — Full query text
- `{output}` — Path to write results (if not in command, reads stdout)
- `{project_root}` — `gen_project_root`
- `{build_dir}` — `compile.build_dir`
- `{dataset_dir}` — `gold.dataset_dir`
- `{sf}` — Scale factor
- `{trace}` — Trace output path (for optimization)

**Without a run_command**, `query_codegen` reports every query as `unverified` and `optimize` refuses to start.

Output: Generated `.cpp` files in `gen_project_root/src/queries/`; results in `artifacts_dir/correctness_report.json`

### 5. `optimize` — Improve Performance

Proposes changes to reduce runtime. Each round:
1. Snapshot the workspace
2. Apply the model's patch
3. Rebuild
4. Re-verify *all* queries (shared storage means one edit breaks everything)
5. Measure median of `repeat_runs` runs
6. Keep if improvement ≥ `min_improvement`, else restore

Configuration:

```json
{
  "optimize": {
    "params": {
      "max_rounds": 4,
      "min_improvement": 0.05,
      "repeat_runs": 3,
      "runtime_pattern": "total runtime: ([0-9.]+)s"
    }
  }
}
```

- **`max_rounds`** — Maximum optimization iterations
- **`min_improvement`** — Keep change only if speedup ≥ this (e.g., 0.05 = 5%)
- **`repeat_runs`** — How many times to run each query (for stable timing)
- **`runtime_pattern`** — Regex to extract runtime from engine output (first group is the duration)

Output: `artifacts_dir/optimization_report.json`

## Input Files

### Schema (`inputs.schema`)

Text description of the database schema:

```
CREATE TABLE lineitem (
  l_orderkey INT,
  l_partkey INT,
  l_quantity INT,
  l_extendedprice FLOAT,
  l_discount FLOAT,
  l_tax FLOAT,
  l_returnflag CHAR,
  l_linestatus CHAR
);
```

### Queries (`inputs.queries`)

One or more SQL queries, separated by `;`:

```sql
SELECT l_returnflag, l_linestatus, COUNT(*) FROM lineitem GROUP BY l_returnflag, l_linestatus;
SELECT AVG(l_quantity) FROM lineitem WHERE l_discount > 0.05;
```

### Statistics (Optional, `inputs.statistics`)

Row counts, column cardinalities, etc. to guide the plan:

```
lineitem: 600572 rows
lineitem.l_returnflag: 2 distinct values
lineitem.l_linestatus: 2 distinct values
```

## Gold Results

The pipeline compares generated query outputs against "gold" — reference results from a known-good system.

### DuckDB Mode (Recommended)

```json
{
  "gold": {
    "mode": "duckdb",
    "duckdb_path": "out/gold/gold.duckdb",
    "dataset_dir": "dataset/sf0.25"
  }
}
```

The database is created once, then reused. Load your data into the `.duckdb` file before the first run.

### Precomputed Mode

```json
{
  "gold": {
    "mode": "precomputed",
    "dir": "gold_results",
    "extension": ".csv"
  }
}
```

Put CSVs in `gold_results/` named by query:

```
gold_results/
├── q1.csv
├── q2.csv
└── q3.csv
```

Results are matched by query ID and checked cell-by-cell.

## Prompts

Prompts are text files in `agent/prompts/` with metadata in `agent/prompting/manifest.json`.

### Editing Prompts

1. Edit the `.txt` file
2. Rebuild the manifest:
   ```bash
   python -m agent.cli prompts build
   ```
3. Re-run: the new prompt invalidates the cache automatically

### Viewing Prompts

```bash
# List available prompts
python -m agent.cli prompts list

# Show a prompt with substitutions
python -m agent.cli prompts show storage_plan_policy --var schema="..." --var queries="..."

# Show just the text
python -m agent.cli prompts show storage_plan_policy --text-only
```

## Caching & Resumption

### How Caching Works

- **Our cache** (`cache.dir`): Memoizes whole module invocations by prompt fingerprint + inputs
- **DSPy's cache**: Underneath, covers individual completions

Editing a prompt file changes its fingerprint → cache entries that used it are invalidated.

### Cache Control

```json
{
  "cache": {
    "enabled": true,
    "dir": "out/.cache",
    "refresh": false
  }
}
```

- **`enabled`** — Use cache at all
- **`refresh`** — Recompute on every lookup while still writing (refreshes stale answers)
- **`dir`** — Where cache lives

### Resumption

The pipeline checks if a stage's outputs still match its inputs. If yes, it skips. If no, it re-runs.

```bash
# Stage already ran? Skip it.
python -m agent.cli run --config my-project.json

# Force re-run even if outputs are current
python -m agent.cli run --config my-project.json --force

# Rebuild only query_codegen and optimize
python -m agent.cli run --config my-project.json --stages query_codegen,optimize
```

## Tracing

All stages can output a detailed execution trace to JSON:

```json
{
  "common": {
    "trace": {
      "path": "out/trace.jsonl",
      "stdout": false,
      "max_field_chars": 4000
    }
  }
}
```

Each line is a JSON object representing a span (start/end, parent/child tree). Useful for:
- Debugging model behavior (what did the RLM try?)
- Cost analysis (tokens per stage)
- Performance profiling (where did time go?)

View with any JSON viewer or post-process with a script:

```bash
# Pretty-print
jq . out/trace.jsonl

# Extract token counts
jq 'select(.event=="span_end") | {stage: .attributes.stage, tokens: .attributes.usage}' out/trace.jsonl
```

## Troubleshooting

### "DSPy call failed: invalid token"

Check your API key:

```bash
echo $OPENAI_API_KEY
# If empty, set it:
export OPENAI_API_KEY="sk-..."
```

### "deno not found" / "Protocol error"

`dspy.RLM` needs Deno:

```bash
deno --version  # Check if installed
```

If not, install it:

```bash
brew install deno          # macOS
apt-get install deno       # Ubuntu/Debian
```

Or get it from https://deno.land/

### "Compilation failed" / "g++ not found"

Install the C++ compiler:

```bash
apt-get install g++         # Ubuntu/Debian
brew install gcc            # macOS
```

Or set a different compiler in config:

```json
{
  "compile": {
    "compiler": "clang++"
  }
}
```

### Queries keep failing correctness checks

1. Check the gold results are correct:
   ```bash
   duckdb out/gold/gold.duckdb < input/allqueries.txt
   ```

2. Look at the first mismatch in `artifacts/correctness_report.json`:
   ```json
   {
     "query_id": "q1",
     "status": "mismatch",
     "first_diff": {
       "row": 4,
       "column": "l_extendedprice",
       "expected": 1234.5,
       "got": 1234.0
     }
   }
   ```

3. Increase `params.max_correctness_rounds` to give the model more chances

4. Check your prompts — is `query_codegen_task` clear about the expected output format?

### "run_command not found" / "optimize won't start"

Make sure your generated engine binary is built and works:

```bash
cd out/gen && cmake . && make
./build/engine --query q1 --out /tmp/result.csv --sf 0.25
cat /tmp/result.csv
```

If it fails, check your `params.run_command` in config.

### Cache invalidation issues

To clear cache and recompute everything:

```bash
rm -rf out/.cache
python -m agent.cli run --config my-project.json
```

To refresh a single stage:

```json
{
  "stages": {
    "query_codegen": {
      "cache": { "refresh": true }
    }
  }
}
```

## Performance Tips

1. **Use a cheaper model for testing** (`gpt-4-turbo` instead of `gpt-4`)
2. **Start with fewer queries** — validate the pipeline works, then scale up
3. **Lower RLM iterations** in early runs:
   ```json
   {
     "rlm": {
       "max_iterations": 10
     }
   }
   ```
4. **Disable stages you don't need**:
   ```json
   {
     "stages": {
       "optimize": { "enabled": false }
     }
   }
   ```
5. **Use `--dry-run`** to validate config before spending tokens

## Next Steps

- Read [README.md](README.md) for architectural details
- Review [agent/examples/config.example.json](examples/config.example.json) for all config options
- Check [tests/agent/](../../tests/agent/) for integration test examples
