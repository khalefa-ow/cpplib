#!/usr/bin/env python3
"""Convert CSV dataset to Parquet format for gold generation."""

import duckdb
from pathlib import Path

dataset_dir = Path("dataset/sf0.25")
csv_files = ["customer.csv", "orders.csv", "region.csv"]

# Initialize DuckDB
db = duckdb.connect(":memory:")

for csv_file in csv_files:
    csv_path = dataset_dir / csv_file
    parquet_name = csv_file.replace(".csv", ".parquet")
    parquet_path = dataset_dir / parquet_name

    if csv_path.exists():
        print(f"Converting {csv_file} to {parquet_name}...")
        # Read CSV and write Parquet directly via SQL
        db.execute(f"""
            COPY (SELECT * FROM read_csv_auto('{csv_path}'))
            TO '{parquet_path}' (FORMAT PARQUET)
        """)
        print(f"  ✓ Created {parquet_path}")
    else:
        print(f"  ! Skipped {csv_file} (not found)")

print("Conversion complete")
