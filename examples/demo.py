#!/usr/bin/env python3
"""
Demo script for cpplib - C++ Code Analysis & Extraction

This demo showcases the main capabilities of cpplib:
- Opening a C++ repository
- Scanning and listing symbols (functions, classes)
- Extracting functions and classes with their dependencies
- Analyzing code structure
"""

import tempfile
import shutil
from pathlib import Path
from cpplib import CPPCodebase, CodePiece


def create_demo_project() -> Path:
    """Create a temporary C++ project for demonstration."""
    temp_dir = Path(tempfile.mkdtemp(prefix="cpplib_demo_"))
    src_dir = temp_dir / "src"
    src_dir.mkdir()

    # Create a math utilities header
    math_h = src_dir / "math_utils.h"
    math_h.write_text("""#ifndef MATH_UTILS_H
#define MATH_UTILS_H

class Calculator {
public:
    int add(int a, int b);
    int subtract(int a, int b);
    int multiply(int a, int b);
};

int fibonacci(int n);
bool isPrime(int num);

#endif  // MATH_UTILS_H
""")

    # Create math utilities implementation
    math_cpp = src_dir / "math_utils.cpp"
    math_cpp.write_text("""#include "math_utils.h"

int Calculator::add(int a, int b) {
    return a + b;
}

int Calculator::subtract(int a, int b) {
    return a - b;
}

int Calculator::multiply(int a, int b) {
    return a * b;
}

int fibonacci(int n) {
    if (n <= 1) return n;
    return fibonacci(n - 1) + fibonacci(n - 2);
}

bool isPrime(int num) {
    if (num <= 1) return false;
    if (num <= 3) return true;
    if (num % 2 == 0 || num % 3 == 0) return false;
    for (int i = 5; i * i <= num; i += 6) {
        if (num % i == 0 || num % (i + 2) == 0) return false;
    }
    return true;
}
""")

    # Create main file
    main_cpp = src_dir / "main.cpp"
    main_cpp.write_text("""#include <iostream>
#include "math_utils.h"

void displayResults() {
    Calculator calc;
    std::cout << "5 + 3 = " << calc.add(5, 3) << std::endl;
    std::cout << "10 - 4 = " << calc.subtract(10, 4) << std::endl;
    std::cout << "7 * 6 = " << calc.multiply(7, 6) << std::endl;
}

int main() {
    std::cout << "=== cpplib Demo ===" << std::endl;
    displayResults();

    std::cout << "\\nFibonacci(10) = " << fibonacci(10) << std::endl;
    std::cout << "Is 17 prime? " << (isPrime(17) ? "Yes" : "No") << std::endl;

    return 0;
}
""")

    # Create CMakeLists.txt
    cmake_file = temp_dir / "CMakeLists.txt"
    cmake_file.write_text("""cmake_minimum_required(VERSION 3.10)
project(cpplib_demo)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(demo
    src/main.cpp
    src/math_utils.cpp
)

target_include_directories(demo PRIVATE src)
""")

    return temp_dir


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def main():
    """Run the cpplib demo."""
    print("\n" + "="*60)
    print("  cpplib - C++ Code Analysis & Extraction Demo")
    print("="*60)

    # Create demo project
    print("\n📁 Creating temporary C++ project...")
    project_dir = create_demo_project()
    print(f"   Project created at: {project_dir}")

    try:
        # Initialize codebase analysis
        print_section("Step 1: Opening C++ Repository")
        print(f"Analyzing: {project_dir / 'src'}")

        codebase = CPPCodebase(str(project_dir / "src"), str(project_dir))
        print("✅ Repository loaded and analyzed!\n")

        # List all functions
        print_section("Step 2: Listing All Functions")
        functions = codebase.get_all_functions()
        if functions:
            for func in functions:
                print(f"  • {func.name} ({func.kind})")
                if func.file_path:
                    print(f"    File: {Path(func.file_path).name}, Line: {func.line_start}")
        else:
            print("  No functions found")

        # List all classes
        print_section("Step 3: Listing All Classes")
        classes = codebase.get_all_classes()
        if classes:
            for cls in classes:
                print(f"  • {cls.name} ({cls.kind})")
                if cls.file_path:
                    print(f"    File: {Path(cls.file_path).name}, Line: {cls.line_start}")
        else:
            print("  No classes found")

        # Extract and show details of a specific function
        print_section("Step 4: Extracting Function Details")
        func_name = "fibonacci"
        print(f"Extracting function: {func_name}")

        piece = codebase.extract_function(func_name)
        if piece:
            print(f"\n✅ Successfully extracted '{func_name}':\n")
            print(f"Name: {piece.name}")
            print(f"Kind: {piece.kind}")
            print(f"Lines: {piece.line_start}-{piece.line_end}")
            if piece.dependencies:
                print(f"Dependencies: {len(piece.dependencies)}")
                for dep in piece.dependencies:
                    print(f"  - {dep}")
            print(f"\nSignature:")
            print("-" * 60)
            print(f"  {piece.signature}")
            print("-" * 60)
            print(f"\nBody (first 10 lines):")
            print("-" * 60)
            body_lines = piece.body.split('\n')[:10]
            for line in body_lines:
                print(f"  {line}")
            if len(piece.body.split('\n')) > 10:
                print("  ...")
            print("-" * 60)
        else:
            print(f"❌ Function '{func_name}' not found")

        # Extract a class
        print_section("Step 5: Extracting Class Details")
        class_name = "Calculator"
        print(f"Extracting class: {class_name}")

        piece = codebase.extract_class(class_name)
        if piece:
            print(f"\n✅ Successfully extracted '{class_name}':\n")
            print(f"Name: {piece.name}")
            print(f"Kind: {piece.kind}")
            print(f"Lines: {piece.line_start}-{piece.line_end}")
            if piece.dependencies:
                print(f"Dependencies: {len(piece.dependencies)}")
                for dep in piece.dependencies:
                    print(f"  - {dep}")
            print(f"\nClass Definition:")
            print("-" * 60)
            print(f"  {piece.signature}")
            print("-" * 60)
            print(f"\nBody (first 20 lines):")
            print("-" * 60)
            for line in piece.body.split('\n')[:20]:
                print(f"  {line}")
            print("-" * 60)
        else:
            print(f"❌ Class '{class_name}' not found")

        # Show symbol lookup
        print_section("Step 6: Symbol Lookup")
        symbol_name = "add"
        print(f"Looking up symbol: {symbol_name}")

        symbol = codebase.get_symbol(symbol_name)
        if symbol:
            print(f"\n✅ Found symbol '{symbol_name}':\n")
            print(f"Name: {symbol.name}")
            print(f"Kind: {symbol.kind}")
            print(f"File: {Path(symbol.file_path).name if symbol.file_path else 'N/A'}")
            print(f"Line: {symbol.line_start}")
            print(f"Scope: {symbol.scope if symbol.scope else 'global'}")
        else:
            print(f"❌ Symbol '{symbol_name}' not found")

        # Show dependency analysis
        print_section("Step 7: Dependency Analysis")
        print("Analyzing dependencies for 'fibonacci' function:\n")

        deps = codebase.get_dependencies("fibonacci")
        if deps:
            print(f"Dependencies found: {len(deps)}")
            for dep_type, dep_list in deps.items():
                if dep_list:
                    print(f"\n  {dep_type.upper()}:")
                    for dep in dep_list:
                        print(f"    - {dep}")
        else:
            print("No dependencies found (fibonacci is self-contained)")

        print_section("Demo Complete!")
        print("✅ cpplib successfully demonstrated:")
        print("   • Repository analysis")
        print("   • Symbol extraction (functions & classes)")
        print("   • Code piece extraction with dependencies")
        print("   • Dependency analysis")
        print()

    finally:
        # Cleanup
        print(f"🧹 Cleaning up temporary project...")
        shutil.rmtree(project_dir, ignore_errors=True)
        print("   Cleanup complete.\n")


if __name__ == "__main__":
    main()
