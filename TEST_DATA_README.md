# Test Data Setup for Query Codegen Pipeline

This document describes the test data infrastructure for testing the query code generation pipeline.

## Overview

The pipeline tests work by comparing generated C++ code output against **gold reference results** from DuckDB. This ensures the generated code produces correct results.

```
SQL Query → DuckDB (gold) → Reference Results (CSV)
            ↓
         Codegen → C++ Code → Compiled Binary → Generated Results
            ↓
         Compare: Generated vs. Gold
         ✓ Match = Query Correct
         ✗ Mismatch = Model Fixes Code & Retries
```

## Generated Files

### Input Data

**Location:** `agent/examples/dataset/sf0.25/`

TPC-H-like benchmark data in CSV format:

- **region.csv** - 5 region records (AFRICA, AMERICA, ASIA, EUROPE, MIDDLE EAST)
- **customer.csv** - 100 customer records with account balance, market segment, region
- **orders.csv** - 500 order records with dates and prices (1992-1998)

### Gold Reference Results

**Location:** `agent/examples/out/gold/`

DuckDB-generated reference outputs for three test queries:

- **q1.csv** - Revenue by market segment (GROUP BY, SUM)
  - 5 rows: HOUSEHOLD, BUILDING, AUTOMOBILE, FURNITURE, MACHINERY segments with total revenue
- **q2.csv** - Order count per region for date range (JOIN, WHERE, GROUP BY)
  - 5 rows: regions with order counts from 1995
- **q3.csv** - Top 20 customers by balance in BUILDING segment (filter, sort, limit)
  - 20 rows: customer names sorted by account balance descending

## How It Works

### 1. Generate Test Data

```bash
cd /home/mk/projects/cpp-tools
uv run python setup_test_data.py [options]
```

**Options:**
- `--data-dir` - Where to write input CSVs (default: `agent/examples/dataset/sf0.25`)
- `--gold-dir` - Where to write gold results (default: `agent/examples/out/gold`)
- `--customer-count` - Number of customers to generate (default: 100)
- `--order-count` - Number of orders to generate (default: 500)
- `--seed` - Random seed for reproducibility (default: 42)

### 2. Run the Pipeline

```bash
./run_pipeline_step_by_step.sh
```

Or directly:

```bash
uv run python -m agent.cli run --config agent/examples/config.example.json
```

### 3. Pipeline Stages

1. **storage_plan** - Design storage layout for the schema
2. **divide** - Create hint levels for ablation study
3. **hppgen** - Generate C++ storage layout headers
4. **query_codegen** - Generate C++ implementations and verify correctness
   - ✅ Generates C++ code
   - ✅ Compiles it
   - ✅ Runs it to get output
   - ✅ Compares output with gold results
   - ✅ If mismatch, model fixes and retries
5. **optimize** - Performance tuning

### 4. Check Results

After running **query_codegen** stage, check:

```bash
cat agent/examples/out/artifacts/query_codegen/correctness_report.json | jq .
```

Each query shows:
- `status` - One of: `matched`, `mismatch`, `compile_failed`, `no_gold`, `unverified`
- `compile_rounds` - Number of compile fixes needed
- `correctness_rounds` - Number of output mismatch fixes needed
- `report` - Details about the last error or match

## Example: Full Correctness Report

```json
{
  "queries": {
    "q1": {
      "query_id": "q1",
      "sql": "SELECT c_mktsegment, SUM(o_totalprice) AS revenue ...",
      "status": "matched",
      "compiled": true,
      "built": true,
      "ran": true,
      "matched": true,
      "compile_rounds": 0,
      "correctness_rounds": 0,
      "runtime_s": 0.001234,
      "report": "MATCH (5 rows, 2 columns)"
    }
  },
  "verified": true,
  "counts": {
    "matched": 3
  }
}
```

## Comparison Logic

The pipeline compares generated output vs. gold using:

- **Row count matching** - Exact row count required
- **Column count matching** - Exact columns in same order
- **Cell value comparison** - Exact match for text, tolerance for floats (1e-6)
- **Header handling** - DuckDB gold has header; generated code doesn't (configurable)
- **Detailed mismatch reporting** - Points to exact cell that differs

**Example mismatch:**

```
MISMATCH: cell value differs
  first difference at row 1, column 2 (revenue)
    expected: '34139630.78'
    actual:   '30000000.00'
  expected shape 5x2, actual 5x2
```

The model uses this precise feedback to fix the C++ code.

## Customizing Test Data

To generate different dataset sizes:

```bash
# Larger dataset
uv run python setup_test_data.py --customer-count 1000 --order-count 5000

# Custom directory
uv run python setup_test_data.py --data-dir my_data --gold-dir my_gold

# Reproducible runs
uv run python setup_test_data.py --seed 12345
```

## Troubleshooting

**Error: `duckdb` not installed**
```bash
pip install duckdb
```

**Gold results not comparing**

Check configuration in `agent/examples/config.example.json`:
```json
{
  "common": {
    "gold": {
      "mode": "duckdb",
      "dataset_dir": "agent/examples/dataset/sf0.25",
      "dir": "agent/examples/out/gold"
    }
  }
}
```

**Regenerate data from scratch**
```bash
rm -rf agent/examples/dataset agent/examples/out/gold
uv run python setup_test_data.py
```

## Next Steps

1. ✅ Test data generated
2. Run: `./run_pipeline_step_by_step.sh`
3. Watch stage 4 (query_codegen) compare outputs
4. View results: `cat agent/examples/out/artifacts/query_codegen/correctness_report.json | jq .`
