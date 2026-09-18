# Expected Output at Each Stage

Here's exactly what you'll see when you run each stage of the pipeline.

---

## Setup: `python -m agent.cli doctor`

```
Checking environment...
✓ deno found at /usr/bin/deno
✓ cmake found at /usr/bin/cmake
✓ g++ found at /usr/bin/g++
✓ OPENAI_API_KEY is set
All checks passed!
```

If any fails, install the tool or set the environment variable.

---

## Setup: `python -m agent.cli prompts build`

```
Building prompt manifest...
✓ Loaded 12 prompts from agent/prompts/
✓ Manifest written to agent/prompting/manifest.json
Done.
```

---

## Dry Run: `python -m agent.cli run --config ... --dry-run`

```
[INFO] Loading config from agent/examples/config.example.json
[INFO] Config is valid
[INFO] Base directory: /home/mk/projects/cpp-tools/agent/examples
[INFO] --dry-run: skipping all stages
```

This is the fastest check — validates config without any API calls.

---

## Stage 1: `storage_plan`

### Command
```bash
python -m agent.cli run --config agent/examples/config.example.json --stages storage_plan
```

### Console Output (First Run)

```
[INFO] Loading config from agent/examples/config.example.json
[INFO] Stage: storage_plan
[INFO] Input fingerprint: 8f2c1b3a...
[INFO] Cache miss, running stage
[INFO] Initializing DSPy RLM...
[INFO] Analyzing schema...
[INFO] Analyzing queries...
[INFO] Invoking storage_plan_policy prompt...
[INFO] LLM Response (tokens: input=2847, output=1234):
[LLM response shown...]
[INFO] Parsing output...
[INFO] Writing output: out/artifacts/storage_plan.json
[INFO] ✓ storage_plan completed in 28.3s
```

### Output File: `out/artifacts/storage_plan.json`

```json
{
  "analysis": {
    "schema_summary": "3 tables: region (5 cols), customer (5 cols), orders (5 cols)",
    "query_patterns": [
      "Aggregation with GROUP BY (Q1, Q2)",
      "Range filter + ORDER BY + LIMIT (Q3)"
    ],
    "join_graph": "customer ← region, customer ← orders",
    "cardinality_estimates": {
      "region": "Low (5 regions)",
      "customer": "Medium (thousands)",
      "orders": "High (millions)"
    ]
  },
  "plan": {
    "storage_model": "column_store",
    "indexing_strategy": "sort_key on join columns",
    "materialization": "customer lookup cache",
    "rationale": "Column store favors aggregations; lookup cache speeds joins"
  },
  "metadata": {
    "timestamp": "2026-09-17T14:23:45Z",
    "model": "openai/gpt-4",
    "tokens": 4081
  }
}
```

### Subsequent Runs (Cached)

```
[INFO] Loading config from agent/examples/config.example.json
[INFO] Stage: storage_plan
[INFO] Input fingerprint: 8f2c1b3a...
[INFO] Cache hit: using stored output
[INFO] ✓ storage_plan completed in 0.1s
```

Fast! Because it's cached.

---

## Stage 2: `divide`

### Command
```bash
python -m agent.cli run --config agent/examples/config.example.json --stages divide
```

### Console Output

```
[INFO] Loading config from agent/examples/config.example.json
[INFO] Stage: divide
[INFO] Input fingerprint: 9a4f2e1c...
[INFO] Cache miss, running stage
[INFO] Input levels: no_hints, organization_level, all_hints
[INFO] Invoking divide_policy prompt...
[INFO] LLM Response (tokens: input=3421, output=892):
[LLM response shown...]
[INFO] Validating level names...
[INFO] ✓ All 3 levels present
[INFO] Writing output: out/artifacts/schema_levels.json
[INFO] ✓ divide completed in 15.2s
```

### Output File: `out/artifacts/schema_levels.json`

```json
{
  "no_hints": {
    "region": {
      "storage": "row",
      "indexes": []
    },
    "customer": {
      "storage": "row",
      "indexes": []
    },
    "orders": {
      "storage": "row",
      "indexes": []
    }
  },
  "organization_level": {
    "region": {
      "storage": "column",
      "indexes": ["r_regionkey"]
    },
    "customer": {
      "storage": "column",
      "indexes": ["c_regionkey"]
    },
    "orders": {
      "storage": "column",
      "indexes": ["o_custkey"]
    }
  },
  "all_hints": {
    "region": {
      "storage": "column",
      "indexes": ["r_regionkey"],
      "materialized_views": ["region_by_name"]
    },
    "customer": {
      "storage": "column",
      "indexes": ["c_regionkey", "c_mktsegment"],
      "sort_key": ["c_regionkey"],
      "cache": true
    },
    "orders": {
      "storage": "column",
      "indexes": ["o_custkey", "o_orderdate"],
      "sort_key": ["o_custkey"]
    }
  }
}
```

This is what gets generated per level — basic has no hints, intermediate adds indexes, full has everything.

---

## Stage 3: `hppgen`

### Command
```bash
python -m agent.cli run --config agent/examples/config.example.json --stages hppgen
```

### Console Output (First Run)

```
[INFO] Loading config from agent/examples/config.example.json
[INFO] Stage: hppgen
[INFO] Active levels: [no_hints]
[INFO] Level: no_hints
[INFO] Generating header for no_hints...
[INFO] Invoking hppgen_policy prompt...
[INFO] LLM Response (tokens: input=2156, output=3421):
[LLM response shown...]
[INFO] Writing: out/gen/include/storage_layout_basic.hpp
[INFO] Compiling...
[INFO] Compilation: SUCCESS
[INFO] ✓ hppgen completed in 45.6s
```

If there are compile errors, it retries:

```
[INFO] Compilation: FAILED
[ERROR] error: unknown type name 'decimal'
[INFO] Attempting fix (round 1/2)...
[INFO] Invoking fix_compile_errors prompt...
[LLM response with fix...]
[INFO] Writing: out/gen/include/storage_layout_basic.hpp
[INFO] Compiling...
[INFO] Compilation: SUCCESS
[INFO] ✓ hppgen completed in 78.3s
```

### Output File: `out/gen/include/storage_layout_basic.hpp`

```cpp
#pragma once
#include <vector>
#include <cstring>
#include <memory>

namespace basic {

// Region table: basic row-oriented layout
struct Region {
    std::vector<int32_t> r_regionkey;
    std::vector<char[25]> r_name;
    std::vector<char[152]> r_comment;
    
    size_t size() const { return r_regionkey.size(); }
};

// Customer table: basic row-oriented layout
struct Customer {
    std::vector<int32_t> c_custkey;
    std::vector<char[25]> c_name;
    std::vector<int32_t> c_regionkey;
    std::vector<char[10]> c_mktsegment;
    std::vector<double> c_acctbal;
    
    size_t size() const { return c_custkey.size(); }
};

// Orders table: basic row-oriented layout
struct Orders {
    std::vector<int32_t> o_orderkey;
    std::vector<int32_t> o_custkey;
    std::vector<char[10]> o_orderdate;
    std::vector<double> o_totalprice;
    std::vector<char> o_orderstatus;
    
    size_t size() const { return o_orderkey.size(); }
};

struct Database {
    Region region;
    Customer customer;
    Orders orders;
};

} // namespace basic
```

The header defines the storage structures in C++.

---

## Stage 4: `query_codegen`

### Command
```bash
python -m agent.cli run --config agent/examples/config.example.json --stages query_codegen
```

### Console Output (First Query, Compile Error)

```
[INFO] Loading config from agent/examples/config.example.json
[INFO] Stage: query_codegen
[INFO] Active levels: [no_hints]
[INFO] Level: no_hints
[INFO] Query: q1 (revenue by market segment)
[INFO] Generating query code...
[INFO] Invoking query_codegen_task prompt...
[LLM response with generated C++ code...]
[INFO] Writing: out/gen/src/queries/q1_no_hints.cpp
[INFO] Compiling...
[ERROR] error: 'SUM' is not a valid function
[INFO] Attempting fix (round 1/3)...
[INFO] Invoking fix_compile_errors prompt...
[LLM response with fix (use std::accumulate)...]
[INFO] Writing: out/gen/src/queries/q1_no_hints.cpp
[INFO] Compiling...
[INFO] Compilation: SUCCESS
[INFO] Running: ./build/engine --query q1 --out /tmp/result.csv --sf 0.25
[INFO] Execution: 0.234s
[INFO] Comparing against gold...
[INFO] ✓ Result matches gold (2 rows, 2 columns)
[INFO] Query: q1 PASSED
```

### Console Output (Second Query, Correctness Mismatch)

```
[INFO] Query: q2 (order count per region)
[INFO] Generating query code...
[INFO] Invoking query_codegen_task prompt...
[LLM response...]
[INFO] Writing: out/gen/src/queries/q2_no_hints.cpp
[INFO] Compiling...
[INFO] Compilation: SUCCESS
[INFO] Running: ./build/engine --query q2 --out /tmp/result.csv --sf 0.25
[INFO] Execution: 0.156s
[INFO] Comparing against gold...
[ERROR] Result mismatch at row 1, column 'n_orders': expected 42, got 40
[INFO] Attempting fix (round 1/3)...
[ERROR] Explanation: The query is missing one join condition
[INFO] Invoking query_codegen_task prompt...
[LLM response with fixed query logic...]
[INFO] Writing: out/gen/src/queries/q2_no_hints.cpp
[INFO] Compiling...
[INFO] Compilation: SUCCESS
[INFO] Running: ./build/engine --query q2 --out /tmp/result.csv --sf 0.25
[INFO] Execution: 0.189s
[INFO] Comparing against gold...
[INFO] ✓ Result matches gold (5 rows, 2 columns)
[INFO] Query: q2 PASSED
```

### Output Files

Generated C++ files in `out/gen/src/queries/`:
- `q1_no_hints.cpp`
- `q2_no_hints.cpp`
- `q3_no_hints.cpp`

Each file contains a `run()` function that:
1. Takes a `Database` reference and parameters
2. Executes the query
3. Returns results as a vector of rows

### Output File: `out/artifacts/correctness_report.json`

```json
{
  "level": "no_hints",
  "queries": [
    {
      "query_id": "q1",
      "status": "verified",
      "compile_rounds": 1,
      "correctness_rounds": 0,
      "rows": 2,
      "columns": 2,
      "runtime_ms": 234,
      "timestamp": "2026-09-17T14:35:22Z"
    },
    {
      "query_id": "q2",
      "status": "verified",
      "compile_rounds": 0,
      "correctness_rounds": 1,
      "rows": 5,
      "columns": 2,
      "runtime_ms": 189,
      "timestamp": "2026-09-17T14:35:48Z"
    },
    {
      "query_id": "q3",
      "status": "verified",
      "compile_rounds": 0,
      "correctness_rounds": 0,
      "rows": 20,
      "columns": 2,
      "runtime_ms": 156,
      "timestamp": "2026-09-17T14:36:01Z"
    }
  ],
  "summary": {
    "total": 3,
    "verified": 3,
    "failed": 0,
    "total_time_s": 42.7
  }
}
```

All queries passed!

---

## Stage 5: `optimize`

### Command
```bash
python -m agent.cli run --config agent/examples/config.example.json --stages optimize
```

### Console Output

```
[INFO] Loading config from agent/examples/config.example.json
[INFO] Stage: optimize
[INFO] Level: no_hints
[INFO] Checking preconditions...
[INFO] ✓ All queries verified
[INFO] Target speedup: 2x, min improvement: 5%
[INFO] Round 1/4
[INFO] Proposing optimization...
[INFO] Invoking optim_w_trace prompt...
[LLM response with proposed optimization...]
[INFO] Applying patch...
[INFO] Rebuilding...
[INFO] Compilation: SUCCESS
[INFO] Re-verifying all queries...
[INFO] ✓ q1 verified (matches gold)
[INFO] ✓ q2 verified (matches gold)
[INFO] ✓ q3 verified (matches gold)
[INFO] Measuring performance (3 runs)...
[INFO] Run 1: 45.2ms
[INFO] Run 2: 44.8ms
[INFO] Run 3: 45.1ms
[INFO] Median: 45.0ms (baseline: 42.7ms = -5.4% slower)
[INFO] Did not meet improvement threshold (5%)
[INFO] Reverting changes...
[INFO] ✓ Workspace restored
[INFO] Round 2/4
[INFO] Proposing new optimization...
[LLM response with different optimization...]
[INFO] Applying patch...
[INFO] Rebuilding...
[INFO] Compilation: SUCCESS
[INFO] Re-verifying all queries...
[INFO] ✓ q1 verified (matches gold)
[INFO] ✓ q2 verified (matches gold)
[INFO] ✓ q3 verified (matches gold)
[INFO] Measuring performance (3 runs)...
[INFO] Run 1: 39.2ms
[INFO] Run 2: 38.9ms
[INFO] Run 3: 39.1ms
[INFO] Median: 39.0ms (baseline: 42.7ms = +8.7% faster)
[INFO] ✓ Met improvement threshold (5%)
[INFO] Keeping changes
[INFO] Round 3/4
[INFO] Proposing further optimization...
[LLM response...]
[INFO] Applying patch...
[INFO] Rebuilding...
[INFO] Compilation: SUCCESS
[INFO] Re-verifying all queries...
[INFO] ✓ q1 verified (matches gold)
[INFO] ✓ q2 verified (matches gold)
[INFO] ✓ q3 verified (matches gold)
[INFO] Measuring performance (3 runs)...
[INFO] Run 1: 39.5ms
[INFO] Run 2: 39.2ms
[INFO] Run 3: 39.3ms
[INFO] Median: 39.3ms (baseline: 39.0ms = -0.8% slower)
[INFO] Did not meet improvement threshold (5%)
[INFO] Reverting changes...
[INFO] ✓ Workspace restored
[INFO] Round 4/4
[INFO] Proposing optimization...
[LLM response...]
[INFO] Applying patch...
[INFO] Compilation: FAILED
[ERROR] error: undefined reference to 'prefetch'
[INFO] Attempting fix...
[LLM response with fix...]
[INFO] Compilation: SUCCESS
[INFO] Re-verifying all queries...
[INFO] ✓ q1 verified (matches gold)
[INFO] ✓ q2 verified (matches gold)
[INFO] ✓ q3 verified (matches gold)
[INFO] Measuring performance (3 runs)...
[INFO] Run 1: 38.2ms
[INFO] Run 2: 38.5ms
[INFO] Run 3: 38.3ms
[INFO] Median: 38.3ms (baseline: 39.0ms = +1.8% slower)
[INFO] Did not meet improvement threshold (5%)
[INFO] Reverting changes...
[INFO] ✓ Workspace restored
[INFO] Optimization budget exhausted (4/4 rounds)
[INFO] ✓ optimize completed in 234.5s
```

### Output File: `out/artifacts/optimization_report.json`

```json
{
  "level": "no_hints",
  "baseline_median_ms": 42.7,
  "final_median_ms": 39.0,
  "improvement_percent": 8.7,
  "rounds": [
    {
      "round": 1,
      "proposed_change": "Add SIMD vectorization hints",
      "status": "rejected",
      "median_ms": 45.0,
      "improvement_percent": -5.4,
      "reason": "Did not meet 5% threshold"
    },
    {
      "round": 2,
      "proposed_change": "Inline customer lookup cache",
      "status": "accepted",
      "median_ms": 39.0,
      "improvement_percent": 8.7,
      "reason": "Meets 5% threshold"
    },
    {
      "round": 3,
      "proposed_change": "Further optimize join order",
      "status": "rejected",
      "median_ms": 39.3,
      "improvement_percent": -0.8,
      "reason": "Did not meet 5% threshold"
    },
    {
      "round": 4,
      "proposed_change": "CPU cache prefetching",
      "status": "rejected",
      "median_ms": 38.3,
      "improvement_percent": 1.8,
      "reason": "Did not meet 5% threshold"
    }
  ],
  "final_code": "out/gen/src/queries/...",
  "timestamp": "2026-09-17T14:40:15Z"
}
```

---

## Full Pipeline Summary

### Directory Structure After Running All Stages

```
agent/examples/
├── out/
│   ├── artifacts/
│   │   ├── storage_plan.json          ← Stage 1 output
│   │   ├── schema_levels.json         ← Stage 2 output
│   │   ├── correctness_report.json    ← Stage 4 output
│   │   └── optimization_report.json   ← Stage 5 output
│   ├── gen/
│   │   ├── CMakeLists.txt
│   │   ├── include/
│   │   │   └── storage_layout_basic.hpp    ← Stage 3 output
│   │   ├── build/
│   │   │   └── engine                      ← Compiled binary
│   │   └── src/
│   │       └── queries/
│   │           ├── q1_no_hints.cpp         ← Stage 4 output
│   │           ├── q2_no_hints.cpp
│   │           └── q3_no_hints.cpp
│   ├── .cache/
│   │   ├── storage_plan/
│   │   ├── divide/
│   │   ├── hppgen/
│   │   ├── query_codegen/
│   │   └── optimize/
│   ├── gold/
│   │   └── gold.duckdb
│   └── trace.jsonl                   ← Execution trace
└── input/
    ├── schema.txt
    ├── allqueries.txt
    └── statistics.txt
```

---

## Performance Metrics

When you run the interactive script, you'll also see:

```
=== Pipeline Overview ===

Artifacts directory:
-rw-r--r-- 1 mk mk  45K Sep 17 14:40 storage_plan.json
-rw-r--r-- 1 mk mk  12K Sep 17 14:40 schema_levels.json
-rw-r--r-- 1 mk mk  8.5K Sep 17 14:40 correctness_report.json
-rw-r--r-- 1 mk mk  6.2K Sep 17 14:40 optimization_report.json

Generated C++ directory:
-rw-r--r-- 1 mk mk 2.3K Sep 17 14:35 storage_layout_basic.hpp
-rw-r--r-- 1 mk mk 1.8K Sep 17 14:36 q1_no_hints.cpp
-rw-r--r-- 1 mk mk 2.1K Sep 17 14:36 q2_no_hints.cpp
-rw-r--r-- 1 mk mk 1.9K Sep 17 14:36 q3_no_hints.cpp
```

---

## What Each Output Tells You

| Stage | Output | What It Means |
|-------|--------|--------------|
| `storage_plan` | `storage_plan.json` | How the LLM decided to organize data (column vs row, indexing, caching) |
| `divide` | `schema_levels.json` | Different hint levels for ablation study |
| `hppgen` | `storage_layout_*.hpp` | Compiled C++ structs that implement the storage layout |
| `query_codegen` | `.cpp` files + `correctness_report.json` | Generated query implementations; all verified against gold |
| `optimize` | `optimization_report.json` | Which optimizations worked, final speedup achieved |

You can inspect any of these files to understand what the pipeline did at each stage.
