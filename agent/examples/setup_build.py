#!/usr/bin/env python3
"""Setup and generate build files for the query engine.

This script:
1. Scans src/queries/ for query implementations
2. Generates CMakeLists.txt dynamically
3. Wraps queries with unique namespaces
4. Generates the query dispatch code
"""

import sys
from pathlib import Path


def get_query_files():
    """Find all query source files."""
    queries_dir = Path("src/queries")
    if not queries_dir.exists():
        return []

    query_files = sorted(queries_dir.glob("q*.cpp"))
    return [(f.stem, f.name) for f in query_files]


def wrap_query(query_file: Path, namespace_name: str) -> str:
    """Wrap a query source file with a unique namespace."""
    if not query_file.exists():
        print(f"Warning: {query_file} not found", file=sys.stderr)
        return ""

    content = query_file.read_text()
    # Replace the namespace declaration
    wrapped = content.replace(
        "namespace queries {",
        f"namespace {namespace_name} {{"
    )
    return wrapped


def generate_cmake(query_files: list[tuple[str, str]]) -> str:
    """Generate CMakeLists.txt based on discovered query files."""
    query_sources = "\n  ".join(f"src/queries/{name}" for _, name in query_files)
    wrapped_sources = "\n  ".join(f"q{i}_wrapped.cpp" for i, _ in enumerate(query_files, 1))

    cmake = f"""cmake_minimum_required(VERSION 3.15)
project(QueryEngine)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Main executable with query dispatch
add_executable(engine
  ${{CMAKE_CURRENT_SOURCE_DIR}}/src/main.cpp
  ${{CMAKE_CURRENT_SOURCE_DIR}}/query_dispatch.cpp
  {wrapped_sources}
)

target_include_directories(engine PRIVATE ${{CMAKE_CURRENT_SOURCE_DIR}})
"""
    return cmake


def generate_dispatch(query_files: list[tuple[str, str]]) -> str:
    """Generate the query dispatch code."""
    forward_decls = "\n".join(
        f"namespace q{i}_namespace {{ void run(const basic::Database& db, std::ostream& out); }}"
        for i, _ in enumerate(query_files, 1)
    )

    dispatch_cases = "\n  ".join(
        f'if (query_id == "{stem}" || query_id == "{i}") return q{i}_namespace::run;'
        for i, (stem, _) in enumerate(query_files, 1)
    )

    dispatch = f'''#include "storage_layout_basic.hpp"
#include <string>
#include <ostream>

{forward_decls}

typedef void (*QueryFunc)(const basic::Database&, std::ostream&);

QueryFunc get_query_func(const std::string& query_id) {{
  {dispatch_cases}
  return nullptr;
}}
'''
    return dispatch


def main():
    source_dir = Path.cwd()

    # Find all query files
    query_files = get_query_files()
    if not query_files:
        print("No query files found in src/queries/", file=sys.stderr)
        return 1

    print(f"Found {len(query_files)} query files: {', '.join(f[0] for f in query_files)}")

    # Generate CMakeLists.txt
    cmake_content = generate_cmake(query_files)
    cmake_path = source_dir / "CMakeLists.txt"
    cmake_path.write_text(cmake_content)
    print(f"✓ Generated {cmake_path}")

    # Wrap each query file
    for i, (stem, filename) in enumerate(query_files, 1):
        query_path = source_dir / "src" / "queries" / filename
        namespace_name = f"q{i}_namespace"
        wrapped = wrap_query(query_path, namespace_name)

        if wrapped:
            wrapped_path = source_dir / f"q{i}_wrapped.cpp"
            wrapped_path.write_text(wrapped)
            print(f"✓ Wrapped {filename} -> {wrapped_path.name}")

    # Generate dispatch code
    dispatch_content = generate_dispatch(query_files)
    dispatch_path = source_dir / "query_dispatch.cpp"
    dispatch_path.write_text(dispatch_content)
    print(f"✓ Generated {dispatch_path}")

    print("Build setup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
