#!/usr/bin/env python3
"""
Demo: C++20/C++23 Support in cpplib

Showcases how to work with different C++ standards:
- Configuring C++ standard
- Feature detection
- Parsing and validating modern C++ code
"""

import tempfile
import shutil
from pathlib import Path
from cpplib import CPPCodebase, CppStandard, StandardConfig, TextReporter, CXX17, CXX20, CXX23


def create_cpp20_project() -> Path:
    """Create a C++20 demo project with modern features."""
    temp_dir = Path(tempfile.mkdtemp(prefix="cpplib_cpp20_"))
    src_dir = temp_dir / "src"
    src_dir.mkdir()

    # C++20 feature: Concepts
    concepts_h = src_dir / "concepts.h"
    concepts_h.write_text("""#ifndef CONCEPTS_H
#define CONCEPTS_H

#include <concepts>

template<std::integral T>
T add(T a, T b) {
    return a + b;
}

template<std::floating_point T>
T divide(T a, T b) {
    return b != 0 ? a / b : 0;
}

template<typename T>
concept Printable = requires(T t) {
    { std::cout << t } -> std::convertible_to<std::ostream&>;
};

#endif
""")

    # C++20 feature: Ranges and Coroutines header
    modern_h = src_dir / "modern.h"
    modern_h.write_text("""#ifndef MODERN_H
#define MODERN_H

#include <concepts>
#include <ranges>
#include <vector>

// C++20 ranges
auto filter_even(const std::vector<int>& nums) {
    return nums | std::views::filter([](int n) { return n % 2 == 0; });
}

// C++20 spaceship operator
class Point {
public:
    int x, y;
    auto operator<=>(const Point&) const = default;
};

// C++20 designated initializers
struct Config {
    int max_threads = 4;
    bool verbose = false;
    std::string name = "default";
};

// C++20 structured bindings with auto
void demonstrate_structured_bindings() {
    auto [x, y] = Point{1, 2};
}

#endif
""")

    # C++20 feature: requires clause
    utils_h = src_dir / "utils.h"
    utils_h.write_text("""#ifndef UTILS_H
#define UTILS_H

#include "concepts.h"
#include <vector>
#include <algorithm>

template<typename T>
requires std::integral<T>
T safe_multiply(T a, T b) {
    return a * b;
}

template<std::ranges::range R>
auto sum_all(R&& r) {
    auto sum = 0;
    for (auto val : r) {
        sum += val;
    }
    return sum;
}

#endif
""")

    # Main file
    main_cpp = src_dir / "main.cpp"
    main_cpp.write_text("""#include <iostream>
#include <vector>
#include "modern.h"
#include "utils.h"

int main() {
    std::cout << "C++20 Features Demo" << std::endl;

    // Concepts
    int result = add(5, 3);
    std::cout << "add(5, 3) = " << result << std::endl;

    // Ranges and spaceship operator
    std::vector<int> nums{1, 2, 3, 4, 5};
    std::cout << "Sum: " << sum_all(nums) << std::endl;

    Point p1{1, 2};
    Point p2{1, 2};
    if (p1 <=> p2 == 0) {
        std::cout << "Points are equal" << std::endl;
    }

    // Designated initializers
    Config cfg{.max_threads = 8, .verbose = true};
    std::cout << "Config threads: " << cfg.max_threads << std::endl;

    return 0;
}
""")

    # CMakeLists.txt
    cmake_file = temp_dir / "CMakeLists.txt"
    cmake_file.write_text("""cmake_minimum_required(VERSION 3.20)
project(cpp20_demo)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(demo src/main.cpp)
target_include_directories(demo PRIVATE src)
""")

    return temp_dir


def create_cpp23_project() -> Path:
    """Create a C++23 demo project (simulated with available C++20 features)."""
    temp_dir = Path(tempfile.mkdtemp(prefix="cpplib_cpp23_"))
    src_dir = temp_dir / "src"
    src_dir.mkdir()

    # Simulated C++23 features (using C++20 where applicable)
    advanced_h = src_dir / "advanced.h"
    advanced_h.write_text("""#ifndef ADVANCED_H
#define ADVANCED_H

#include <concepts>
#include <utility>

// C++20/23: Advanced concepts
template<typename T, typename U>
concept SameSize = sizeof(T) == sizeof(U);

template<typename T>
concept HasOperatorPlus = requires(T a, T b) {
    { a + b } -> std::convertible_to<T>;
};

// Helper function using concepts
template<HasOperatorPlus T>
T apply_twice(T value, T increment) {
    return (value + increment) + increment;
}

#endif
""")

    main_cpp = src_dir / "main.cpp"
    main_cpp.write_text("""#include <iostream>
#include "advanced.h"

int main() {
    std::cout << "C++23-Ready Code" << std::endl;

    int result = apply_twice(10, 5);
    std::cout << "Result: " << result << std::endl;

    return 0;
}
""")

    cmake_file = temp_dir / "CMakeLists.txt"
    cmake_file.write_text("""cmake_minimum_required(VERSION 3.20)
project(cpp23_demo)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(demo src/main.cpp)
target_include_directories(demo PRIVATE src)
""")

    return temp_dir


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def show_standard_info() -> None:
    """Display information about supported C++ standards."""
    print_section("Supported C++ Standards")

    print("Available Standards:")
    for std in StandardConfig.SUPPORTED:
        print(f"  • {std.display_name:8} - {std.to_cmake_flag():3} - {std.to_compiler_flag()}")

    print("\nDefault Standard:", StandardConfig.DEFAULT.display_name)


def show_feature_availability() -> None:
    """Show which features are available in each standard."""
    print_section("Feature Availability by Standard")

    # Some key features
    features_to_check = ["auto", "lambda", "constexpr", "concepts", "ranges", "coroutines"]

    print("Feature Support Matrix:\n")
    print(f"{'Feature':<20}", end="")
    for std in [CXX17, CXX20, CXX23]:
        print(f"{std.display_name:<12}", end="")
    print()
    print("-" * 56)

    for feature in features_to_check:
        print(f"{feature:<20}", end="")
        for std in [CXX17, CXX20, CXX23]:
            supported = StandardConfig.has_feature(std, feature)
            status = "✓" if supported else "✗"
            print(f"{status:<12}", end="")
        print()


def test_cpp20_compilation() -> None:
    """Test compilation of C++20 code."""
    print_section("Test 1: C++20 Project Compilation")

    project_dir = create_cpp20_project()
    print(f"Created C++20 project at: {project_dir}\n")

    try:
        # Analyze with C++20
        print("Analyzing with C++20 standard...")
        codebase = CPPCodebase(str(project_dir / "src"), str(project_dir), CXX20)
        print(f"✓ Analysis complete (Standard: {codebase.get_cpp_standard_display()})\n")

        # Show features
        reporter = TextReporter(compact=True)
        functions = codebase.get_all_functions()
        classes = codebase.get_all_classes()
        files = codebase.file_index.list_files()

        print(reporter.codebase_statistics(len(functions), len(classes), len(files)))
        print()
        print(reporter.symbols_summary(functions, classes))
        print()

        # Test compilation
        print("Compiling with C++20...")
        success, msg = codebase.validate()
        print(reporter.validation_result(success, msg))

        # Check C++20 features
        print("\nC++20 Features Supported:")
        if codebase.check_feature_support("concepts"):
            print("  ✓ Concepts")
        if codebase.check_feature_support("ranges"):
            print("  ✓ Ranges")
        if codebase.check_feature_support("spaceship_operator"):
            print("  ✓ Spaceship Operator (<=>)")
        if codebase.check_feature_support("designated_initializers"):
            print("  ✓ Designated Initializers")

    except Exception as e:
        print(f"Error: {e}")

    finally:
        shutil.rmtree(project_dir, ignore_errors=True)


def test_cpp23_ready() -> None:
    """Test C++23-ready code."""
    print_section("Test 2: C++23-Ready Code Analysis")

    project_dir = create_cpp23_project()
    print(f"Created C++23 project at: {project_dir}\n")

    try:
        # Note: We use C++20 since full C++23 support may not be available
        print("Analyzing with C++20 (C++23 ready)...")
        codebase = CPPCodebase(str(project_dir / "src"), str(project_dir), CXX20)
        print(f"✓ Analysis complete\n")

        reporter = TextReporter(compact=True)
        functions = codebase.get_all_functions()

        print(f"Found {len(functions)} functions")
        for func in functions:
            print(f"  • {func.name}")

    except Exception as e:
        print(f"Error: {e}")

    finally:
        shutil.rmtree(project_dir, ignore_errors=True)


def test_standard_switching() -> None:
    """Test switching between standards."""
    print_section("Test 3: Dynamic Standard Switching")

    project_dir = create_cpp20_project()
    print(f"Testing with C++20 project\n")

    try:
        # Start with C++17
        print("1. Analyzing with C++17...")
        codebase = CPPCodebase(str(project_dir / "src"), str(project_dir), CXX17)
        print(f"   Current: {codebase.get_cpp_standard_display()}")
        reporter = TextReporter(compact=True)
        success, msg = codebase.validate()
        print(f"   Result: {msg}\n")

        # Switch to C++20
        print("2. Switching to C++20...")
        codebase.set_cpp_standard(CXX20)
        print(f"   Current: {codebase.get_cpp_standard_display()}")
        success, msg = codebase.validate()
        print(f"   Result: {msg}\n")

        # Show available standards
        print("3. Available standards:")
        for std in codebase.validator.get_supported_standards():
            print(f"   • {std.display_name}")

    except Exception as e:
        print(f"Error: {e}")

    finally:
        shutil.rmtree(project_dir, ignore_errors=True)


def main():
    """Run the C++ standards demo."""
    print("\n" + "="*70)
    print("  cpplib - C++20/C++23 Support Demo")
    print("="*70)

    # Show standard information
    show_standard_info()

    # Show feature matrix
    show_feature_availability()

    # Test C++20 compilation
    test_cpp20_compilation()

    # Test C++23 ready code
    test_cpp23_ready()

    # Test standard switching
    test_standard_switching()

    # Summary
    print_section("Summary")
    print("✅ cpplib now supports:\n")
    print("  • C++98, C++11, C++14, C++17, C++20, C++23")
    print("  • Dynamic standard switching")
    print("  • Feature detection per standard")
    print("  • CMake integration with standard flags")
    print()
    print("Usage:")
    print("  from cpplib import CPPCodebase, CXX20")
    print("  codebase = CPPCodebase('src', '.', CXX20)")
    print("  codebase.validate()  # Compile with C++20")
    print()


if __name__ == "__main__":
    main()
