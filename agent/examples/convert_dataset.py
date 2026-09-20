#!/usr/bin/env python3
"""Convert CSV dataset to Parquet format for gold generation.

Handles CRLF line endings in CSV files by normalizing them to LF.
"""

import duckdb
from pathlib import Path
import tempfile

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

        # Read CSV and normalize line endings
        csv_text = csv_path.read_text(encoding='utf-8')

        # Normalize CRLF to LF (remove all CR characters)
        cleaned_text = csv_text.replace('\r\n', '\n').replace('\r', '')

        # Write to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as tmp:
            tmp.write(cleaned_text)
            tmp_path = tmp.name

        try:
            # Read cleaned CSV and convert to Parquet
            db.execute(f"""
                COPY (SELECT * FROM read_csv_auto('{tmp_path}'))
                TO '{parquet_path}' (FORMAT PARQUET)
            """)
            print(f"  ✓ Created {parquet_path}")
        finally:
            # Clean up temp file
            Path(tmp_path).unlink(missing_ok=True)
    else:
        print(f"  ! Skipped {csv_file} (not found)")

print("Conversion complete")
