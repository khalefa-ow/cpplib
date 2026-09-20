#!/usr/bin/env python3
"""Complete test data setup: generate input data and gold reference results.

This script:
1. Generates sample TPC-H-like data (region, customer, orders)
2. Uses DuckDB to execute reference queries
3. Saves gold results for comparison during pipeline testing
"""

import argparse
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("Error: duckdb not installed. Install with: pip install duckdb")
    sys.exit(1)


def generate_region(count: int = 5) -> list[dict]:
    """Generate region data."""
    regions = ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]
    return [
        {
            "r_regionkey": i,
            "r_name": regions[i % len(regions)],
        }
        for i in range(count)
    ]


def generate_customer(count: int = 100, num_regions: int = 5) -> list[dict]:
    """Generate customer data."""
    segments = ["BUILDING", "AUTOMOBILE", "FURNITURE", "MACHINERY", "HOUSEHOLD"]
    names = [
        "Alice", "Bob", "Charlie", "Diana", "Eve",
        "Frank", "Grace", "Henry", "Iris", "Jack"
    ]

    customers = []
    for i in range(count):
        customers.append({
            "c_custkey": i,
            "c_name": f"{random.choice(names)} Customer {i}",
            "c_address": f"{i} Main St",
            "c_nationkey": i % 25,
            "c_phone": f"555-{random.randint(1000, 9999)}",
            "c_acctbal": round(random.uniform(-10000, 100000), 2),
            "c_mktsegment": random.choice(segments),
            "c_regionkey": i % num_regions,
            "c_comment": f"Customer {i} comment",
        })
    return customers


def generate_orders(
    count: int = 500,
    num_customers: int = 100,
    start_date: date = date(1992, 1, 1),
    end_date: date = date(1998, 12, 31),
) -> list[dict]:
    """Generate orders data."""
    orders = []
    date_range = (end_date - start_date).days

    statuses = ["O", "F", "P"]

    for i in range(count):
        order_date = start_date + timedelta(days=random.randint(0, date_range))
        orders.append({
            "o_orderkey": i,
            "o_custkey": random.randint(0, num_customers - 1),
            "o_orderstatus": random.choice(statuses),
            "o_totalprice": round(random.uniform(800, 500000), 2),
            "o_orderdate": order_date.strftime("%Y%m%d"),
            "o_orderpriority": random.choice(["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"]),
            "o_clerk": f"Clerk#{random.randint(1, 100)}",
            "o_shippriority": random.randint(0, 1),
            "o_comment": f"Order {i} comment",
        })
    return orders


def write_csv(filepath: Path, rows: list[dict]) -> int:
    """Write rows to a CSV file. Returns number of rows written."""
    if not rows:
        return 0

    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def generate_input_data(
    output_dir: Path,
    customer_count: int,
    order_count: int,
) -> None:
    """Generate input data files."""
    print("📊 Generating input data...\n")

    regions = generate_region(count=5)
    customers = generate_customer(count=customer_count, num_regions=5)
    orders = generate_orders(
        count=order_count,
        num_customers=customer_count,
        start_date=date(1992, 1, 1),
        end_date=date(1998, 12, 31),
    )

    n_regions = write_csv(output_dir / "region.csv", regions)
    n_customers = write_csv(output_dir / "customer.csv", customers)
    n_orders = write_csv(output_dir / "orders.csv", orders)

    print(f"  ✓ region.csv: {n_regions} rows")
    print(f"  ✓ customer.csv: {n_customers} rows")
    print(f"  ✓ orders.csv: {n_orders} rows")


def generate_gold_results(
    data_dir: Path,
    output_dir: Path,
) -> None:
    """Generate gold reference results using DuckDB."""
    print("\n🔍 Generating gold reference results with DuckDB...\n")

    # Check input files exist
    for filename in ["region.csv", "customer.csv", "orders.csv"]:
        path = data_dir / filename
        if not path.exists():
            print(f"❌ Error: {path} not found")
            sys.exit(1)

    # Connect to DuckDB
    conn = duckdb.connect(":memory:")

    # Load CSV files
    conn.execute(f"CREATE TABLE region AS SELECT * FROM read_csv_auto('{data_dir / 'region.csv'}')")
    conn.execute(f"CREATE TABLE customer AS SELECT * FROM read_csv_auto('{data_dir / 'customer.csv'}')")
    conn.execute(f"CREATE TABLE orders AS SELECT * FROM read_csv_auto('{data_dir / 'orders.csv'}')")

    # Define queries
    queries = {
        "q1": """
            SELECT c_mktsegment, SUM(o_totalprice) AS revenue
            FROM customer JOIN orders ON c_custkey = o_custkey
            GROUP BY c_mktsegment
            ORDER BY revenue DESC
        """,
        "q2": """
            SELECT r_name, COUNT(*) AS n_orders
            FROM region
              JOIN customer ON r_regionkey = c_regionkey
              JOIN orders   ON c_custkey  = o_custkey
            WHERE o_orderdate >= '19950101' AND o_orderdate <= '19951231'
            GROUP BY r_name
            ORDER BY n_orders DESC
        """,
        "q3": """
            SELECT c_name, c_acctbal
            FROM customer
            WHERE c_mktsegment = 'BUILDING'
            ORDER BY c_acctbal DESC
            LIMIT 20
        """,
    }

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Execute queries and save results
    for query_id, query_text in queries.items():
        try:
            result = conn.execute(query_text).fetchall()
            col_names = [desc[0] for desc in conn.description]

            # Write to CSV with header
            output_file = output_dir / f"{query_id}.csv"
            with open(output_file, "w") as f:
                f.write(",".join(col_names) + "\n")
                for row in result:
                    # Format values (handle floats with 2 decimal places)
                    formatted_row = []
                    for val in row:
                        if isinstance(val, float):
                            formatted_row.append(f"{val:.2f}")
                        elif val is None:
                            formatted_row.append("")
                        else:
                            formatted_row.append(str(val))
                    f.write(",".join(formatted_row) + "\n")

            print(f"  ✓ {query_id}.csv: {len(result)} row(s)")

        except Exception as e:
            print(f"  ❌ {query_id}: {e}")
            conn.close()
            sys.exit(1)

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Generate test data and gold reference results"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("agent/examples/dataset/sf0.25"),
        help="Output directory for input CSV files",
    )
    parser.add_argument(
        "--gold-dir",
        type=Path,
        default=Path("agent/examples/out/gold"),
        help="Output directory for gold reference results",
    )
    parser.add_argument(
        "--customer-count",
        type=int,
        default=100,
        help="Number of customer records to generate",
    )
    parser.add_argument(
        "--order-count",
        type=int,
        default=500,
        help="Number of order records to generate",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility",
    )

    args = parser.parse_args()
    random.seed(args.seed)

    print("🏃 Test Data Setup\n")
    print(f"  Input dir: {args.data_dir}")
    print(f"  Gold dir: {args.gold_dir}")
    print(f"  Customers: {args.customer_count}")
    print(f"  Orders: {args.order_count}")
    print()

    # Generate input data
    generate_input_data(args.data_dir, args.customer_count, args.order_count)

    # Generate gold reference results
    generate_gold_results(args.data_dir, args.gold_dir)

    print(f"\n✅ Setup complete!")
    print(f"\nData files created:")
    print(f"  {args.data_dir}/")
    print(f"    ├── region.csv")
    print(f"    ├── customer.csv")
    print(f"    └── orders.csv")
    print(f"\nGold reference results created:")
    print(f"  {args.gold_dir}/")
    print(f"    ├── q1.csv")
    print(f"    ├── q2.csv")
    print(f"    └── q3.csv")
    print(f"\nNext: Run the pipeline with:")
    print(f"  ./run_pipeline_step_by_step.sh")


if __name__ == "__main__":
    main()
