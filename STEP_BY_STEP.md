# Running the Agent Pipeline Step-by-Step

This guide walks you through running the DSPy pipeline stage by stage, with explanations of what happens at each step.

## Setup (5 minutes)

### 1. Check Your Environment

```bash
python -m agent.cli doctor
```

You should see all checks pass. If not, install the missing tool (Deno, cmake, g++, or set your API key).

### 2. (Optional) Resync Prompt Metadata

Prompt text lives inline in `agent/prompting/manifest.json` (each entry has a
`text` field), so there's nothing to rebuild unless you've just edited a
prompt. After editing one (via `prompts set` or by hand), resync its derived
`placeholders`:

```bash
python -m agent.cli prompts build
```

### 3. View Your Config

```bash
python -m agent.cli config show agent/examples/config.example.json
```

This shows the full config with all defaults filled in. The example config is ready to use — it points to toy data in `agent/examples/input/`.

---

## Running the Pipeline

The pipeline has 5 stages that run in dependency order:

```
storage_plan
    ↓
divide
    ↓
hppgen
    ↓
query_codegen
    ↓
optimize
```

You can run them all at once, or step through one at a time. **Let's start with a dry run** (no API calls):

### Dry Run: Check Everything

```bash
python -m agent.cli run --config agent/examples/config.example.json --dry-run
```

This validates the config and checks syntax without calling any LLM. You should see:

```
[INFO] Loading config from agent/examples/config.example.json
[INFO] Config is valid
[INFO] --dry-run: skipping all stages
```

If there are errors, the config or input files have problems. Fix them and try again.

---

## Stage 1: `storage_plan`

**What it does:** Reads the schema and queries, reasons about how to lay out data in memory.

**Input:** Schema, queries, optional statistics  
**Output:** `out/artifacts/storage_plan.json` (the reasoning + plan)

### Run It

```bash
python -m agent.cli run --config agent/examples/config.example.json --stages storage_plan
```

**What to expect:**
- First run takes ~20-30 seconds (LLM call + caching)
- Subsequent runs are instant (cached)
- Output file appears at `agent/examples/out/artifacts/storage_plan.json`

### Check the Output

```bash
cat agent/examples/out/artifacts/storage_plan.json | jq .
```

You should see:
- `analysis` — The model's reasoning about the schema
- `plan` — The proposed storage layout (e.g., column-oriented, row-oriented, hybrid)
- `rationale` — Why that layout was chosen

---

## Stage 2: `divide`

**What it does:** Splits the plan into multiple schema levels (for ablation: no hints → basic hints → all hints).

**Input:** `storage_plan.json`  
**Output:** `out/artifacts/schema_levels.json`

### Run It

```bash
python -m agent.cli run --config agent/examples/config.example.json --stages divide
```

**What to expect:**
- Should be fast (small LLM call)
- Creates `schema_levels.json` with the levels defined in config:
  - `no_hints`
  - `organization_level`
  - `all_hints`

### Check the Output

```bash
cat agent/examples/out/artifacts/schema_levels.json | jq .
```

You should see one key per level with schema hints for that level.

---

## Stage 3: `hppgen`

**What it does:** Generates C++ header files for the storage layout, compiles them, and fixes errors.

**Input:** `schema_levels.json`  
**Output:** Compiled headers in `out/gen/include/storage_layout_*.hpp`

### Important: Choose Your Level

The example config runs only `no_hints`:

```json
{
  "hppgen": {
    "active_levels": ["no_hints"]
  }
}
```

This avoids generating headers for all 3 levels (saves time). Once you understand the pipeline, you can compare levels by running with `active_levels: ["no_hints", "organization_level", "all_hints"]`.

### Run It

```bash
python -m agent.cli run --config agent/examples/config.example.json --stages hppgen
```

**What to expect:**
- Takes 30-60 seconds per level (code generation + compilation)
- If there are compile errors, it retries up to `compile.max_fix_rounds` times (default: 2)
- Header file appears at `out/gen/include/storage_layout_basic.hpp`

### Check the Output

```bash
cat agent/examples/out/gen/include/storage_layout_basic.hpp | head -50
```

You should see C++ struct definitions for the storage layout (e.g., `struct Region`, `struct Customer`).

### Try Compiling It

```bash
cd agent/examples/out/gen
cmake .
make
```

If the headers compiled successfully, you're on track.

---

## Stage 4: `query_codegen`

**What it does:** Generates a `.cpp` file for each query, compiles it, and runs it against gold results.

**Input:** Headers from `hppgen`, schema levels  
**Output:** Generated `.cpp` files, `out/artifacts/correctness_report.json`

### Important: Gold Results

The example config uses DuckDB's built-in engine to compute gold results:

```json
{
  "gold": {
    "mode": "duckdb",
    "duckdb_path": "out/gold/gold.duckdb",
    "dataset_dir": "dataset/sf0.25"
  }
}
```

**Before running**, you need to create the gold database and load your data:

```bash
cd agent/examples
duckdb out/gold/gold.duckdb < input/schema.txt
# Then load your data:
# duckdb out/gold/gold.duckdb -c "INSERT INTO region VALUES (1, 'Americas', '...');"
# etc.
```

For testing, you can use **precomputed gold results** instead:

```json
{
  "gold": {
    "mode": "precomputed",
    "dir": "agent/examples/gold_results"
  }
}
```

Then put expected CSVs in `gold_results/q1.csv`, `gold_results/q2.csv`, etc.

### Run It (With Mock Gold)

For now, let's skip this stage and focus on understanding the pipeline. When you're ready, update your config with gold results.

---

## Stage 5: `optimize`

**What it does:** Proposes changes to improve query runtime, measures them, and keeps only improvements ≥ `min_improvement`.

**Input:** Compiled queries that pass correctness checks  
**Output:** `out/artifacts/optimization_report.json`

### Prerequisites

- All queries must compile and match gold results (from `query_codegen`)
- Your engine binary must be built and runnable via `params.run_command`

### How It Works

Each round:
1. Snapshot the workspace
2. Model proposes a change
3. Rebuild and re-verify all queries
4. Measure median of `repeat_runs` runs
5. Keep if improvement ≥ `min_improvement`, else revert

---

## End-to-End: Full Pipeline

Once you understand each stage, run the whole thing:

```bash
python -m agent.cli run --config agent/examples/config.example.json
```

Or with force (recomputes even if cached):

```bash
python -m agent.cli run --config agent/examples/config.example.json --force
```

---

## Debugging Tips

### See What a Stage Would Do

```bash
python -m agent.cli run --config agent/examples/config.example.json --stages storage_plan --dry-run
```

### Force a Specific Stage to Recompute

```bash
# Remove its cache and re-run
rm -rf agent/examples/out/.cache/storage_plan
python -m agent.cli run --config agent/examples/config.example.json --stages storage_plan
```

### See the Full Config (With Defaults)

```bash
python -m agent.cli config show agent/examples/config.example.json
```

### View a Prompt

```bash
python -m agent.cli prompts show storage_plan_policy --raw
```

### Check Artifacts

All outputs go to `out/artifacts/`:

```bash
ls -la agent/examples/out/artifacts/
```

---

## Next Steps

1. **Run `storage_plan`** — Understand the storage reasoning
2. **Run `divide`** — See the hint levels
3. **Run `hppgen`** — Generate and compile headers
4. **Set up gold results** — Load data into DuckDB or create precomputed CSVs
5. **Run `query_codegen`** — Generate and verify queries
6. **Set up your engine** — Build and run it through `params.run_command`
7. **Run `optimize`** — Improve performance

See [agent/USAGE.md](agent/USAGE.md) for full details on config options and troubleshooting.
