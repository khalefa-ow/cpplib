#!/usr/bin/env python3
"""
Demo script for cpplib validation and error handling

Shows how cpplib handles:
- Broken/invalid C++ code
- Syntax validation
- CMake-based compilation
- Error reporting and diagnostics
"""

import tempfile
import shutil
from pathlib import Path
from cpplib import CPPCodebase
from cpplib.validator.cmake_validator import CMakeValidator


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def test_syntax_checking() -> None:
    """Test syntax checking without full compilation."""
    print_section("Test 1: Syntax Validation")

    validator = CMakeValidator(Path.cwd())

    # Valid C++ code
    valid_code = """
    int add(int a, int b) {
        return a + b;
    }
    """

    print("✓ Checking VALID C++ code:")
    valid, msg = validator.check_syntax(valid_code)
    print(f"  Result: {msg}\n")

    # Invalid C++ code - missing semicolon
    invalid_code1 = """
    int add(int a, int b) {
        return a + b
    }
    """

    print("✗ Checking INVALID C++ code (missing semicolon):")
    valid, msg = validator.check_syntax(invalid_code1)
    if not valid:
        print(f"  ❌ Error detected:")
        print(f"     {msg[:150]}...\n")
    else:
        print(f"  {msg}\n")

    # Invalid C++ code - undefined type
    invalid_code2 = """
    void process() {
        UndefinedType var;
        std::cout << var << std::endl;
    }
    """

    print("✗ Checking INVALID C++ code (undefined type):")
    valid, msg = validator.check_syntax(invalid_code2)
    if not valid:
        print(f"  ❌ Error detected:")
        print(f"     {msg[:150]}...\n")
    else:
        print(f"  {msg}\n")


def test_broken_project() -> None:
    """Test parsing broken C++ project."""
    print_section("Test 2: Parsing Broken Project")

    temp_dir = Path(tempfile.mkdtemp(prefix="cpplib_broken_"))
    src_dir = temp_dir / "src"
    src_dir.mkdir()

    print(f"Created test project: {temp_dir}\n")

    # Create a broken header
    bad_header = src_dir / "broken.h"
    bad_header.write_text("""#ifndef BROKEN_H
#define BROKEN_H

class BrokenClass {
    // Missing semicolon and closing brace
    int member
public:
    void method()

#endif
""")

    # Create a file with syntax errors
    bad_cpp = src_dir / "broken.cpp"
    bad_cpp.write_text("""#include "broken.h"

void BrokenClass::method() {
    int x = 42  // Missing semicolon
    std::cout << x << std::endl;
}

int main() {
    BrokenClass obj;
    obj.method()  // Missing semicolon
    return 0;
}
""")

    # Create CMakeLists.txt
    cmake_file = temp_dir / "CMakeLists.txt"
    cmake_file.write_text("""cmake_minimum_required(VERSION 3.10)
project(broken_demo)

set(CMAKE_CXX_STANDARD 17)

add_executable(broken src/broken.cpp)
target_include_directories(broken PRIVATE src)
""")

    try:
        # Try to analyze the broken project
        print("Attempting to analyze broken C++ code...")
        codebase = CPPCodebase(str(src_dir), str(temp_dir))
        print("✓ AST parsing completed (parser is error-tolerant)\n")

        # Try validation
        print("Attempting to compile broken code...\n")
        success, msg = codebase.validate()

        if not success:
            print("❌ Compilation FAILED (as expected):\n")
            print(msg[:500])
            print("\n" + "-"*70)
            errors = codebase.validator.get_build_errors()
            if errors:
                print("Full error output:")
                print(errors[:800])
        else:
            print(f"✓ {msg}")

    except Exception as e:
        print(f"⚠️  Exception during analysis: {type(e).__name__}")
        print(f"   {str(e)[:200]}\n")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\nCleaned up test project")


def test_successful_build() -> None:
    """Test a project that compiles successfully."""
    print_section("Test 3: Successful Compilation")

    temp_dir = Path(tempfile.mkdtemp(prefix="cpplib_valid_"))
    src_dir = temp_dir / "src"
    src_dir.mkdir()

    print(f"Created test project: {temp_dir}\n")

    # Create valid code
    header = src_dir / "math.h"
    header.write_text("""#ifndef MATH_H
#define MATH_H

class Math {
public:
    int add(int a, int b);
    int multiply(int a, int b);
};

#endif
""")

    impl = src_dir / "math.cpp"
    impl.write_text("""#include "math.h"

int Math::add(int a, int b) {
    return a + b;
}

int Math::multiply(int a, int b) {
    return a * b;
}
""")

    main_cpp = src_dir / "main.cpp"
    main_cpp.write_text("""#include <iostream>
#include "math.h"

int main() {
    Math m;
    std::cout << "10 + 5 = " << m.add(10, 5) << std::endl;
    std::cout << "10 * 5 = " << m.multiply(10, 5) << std::endl;
    return 0;
}
""")

    cmake_file = temp_dir / "CMakeLists.txt"
    cmake_file.write_text("""cmake_minimum_required(VERSION 3.10)
project(valid_demo)

set(CMAKE_CXX_STANDARD 17)

add_executable(valid src/main.cpp src/math.cpp)
target_include_directories(valid PRIVATE src)
""")

    try:
        print("Analyzing valid C++ project...")
        codebase = CPPCodebase(str(src_dir), str(temp_dir))
        print("✓ Analysis completed\n")

        print("Compiling project...")
        success, msg = codebase.validate()

        if success:
            print(f"✅ Build SUCCESS: {msg}")
            print(f"\n   Binary created at: {temp_dir / 'build' / 'valid'}")

            # Show the symbols found
            functions = codebase.get_all_functions()
            classes = codebase.get_all_classes()
            print(f"\n   Found {len(functions)} functions and {len(classes)} classes")
            print(f"   Functions: {', '.join([f.name for f in functions])}")
            print(f"   Classes: {', '.join([c.name for c in classes])}")
        else:
            print(f"❌ Build FAILED: {msg}")

    except Exception as e:
        print(f"⚠️  Exception: {type(e).__name__}: {str(e)[:200]}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\nCleaned up test project")


def test_parsing_tolerant_behavior() -> None:
    """Test that parser is error-tolerant."""
    print_section("Test 4: Parser Error Tolerance")

    temp_dir = Path(tempfile.mkdtemp(prefix="cpplib_tolerance_"))
    src_dir = temp_dir / "src"
    src_dir.mkdir()

    print("Testing parser's error tolerance...\n")

    # Partially valid code (some syntax errors but some parseable parts)
    partial = src_dir / "partial.cpp"
    partial.write_text("""#include <iostream>

// This function has errors but structure is identifiable
int fibonacci(int n {
    if (n <= 1) return n;
    return fibonacci(n - 1) + fibonacci(n - 2);
}

// This one is valid
int add(int a, int b) {
    return a + b;
}

// Missing closing brace and semicolon
int main() {
    std::cout << "Hello" << std::endl
""")

    cmake_file = temp_dir / "CMakeLists.txt"
    cmake_file.write_text("""cmake_minimum_required(VERSION 3.10)
project(tolerance_demo)
set(CMAKE_CXX_STANDARD 17)
add_executable(demo src/partial.cpp)
""")

    try:
        print("Parsing file with partial syntax errors...")
        codebase = CPPCodebase(str(src_dir), str(temp_dir))

        functions = codebase.get_all_functions()
        classes = codebase.get_all_classes()

        print(f"✓ Parser completed (error-tolerant)\n")
        print(f"Found {len(functions)} functions:")
        for func in functions:
            print(f"  • {func.name} at line {func.line_start}")

        print(f"\nFound {len(classes)} classes:")
        if not classes:
            print("  (none)")

        print(f"\nNote: Parser extracts valid structures despite syntax errors")
        print(f"This allows analysis of partially-broken code")

    except Exception as e:
        print(f"Exception: {type(e).__name__}: {str(e)[:300]}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    """Run validation demos."""
    print("\n" + "="*70)
    print("  cpplib - Validation & Error Handling Demo")
    print("="*70)
    print("\nThis demo showcases cpplib's error handling capabilities:")
    print("  • Syntax validation (g++/clang++ checks)")
    print("  • CMake-based compilation")
    print("  • Error reporting and diagnostics")
    print("  • Error-tolerant parsing")

    # Test syntax checking
    test_syntax_checking()

    # Test parsing broken code
    test_broken_project()

    # Test successful compilation
    test_successful_build()

    # Test parser tolerance
    test_parsing_tolerant_behavior()

    print_section("Demo Complete!")
    print("✅ cpplib Validation Features Summary:\n")
    print("1. ✓ Syntax checking (without full compilation)")
    print("2. ✓ CMake-based full project compilation")
    print("3. ✓ Detailed error reporting and diagnostics")
    print("4. ✓ Error-tolerant parsing (extracts valid structures)")
    print("5. ✓ Build output and error log access\n")


if __name__ == "__main__":
    main()
