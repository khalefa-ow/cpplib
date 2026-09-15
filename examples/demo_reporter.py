#!/usr/bin/env python3
"""
Demo: Text Reporter for Compact Output

Shows how to use the TextReporter and AnalysisReporter
to generate compact text output for cpplib analysis results.
"""

import tempfile
import shutil
from pathlib import Path
from cpplib import CPPCodebase, TextReporter, AnalysisReporter


def create_demo_project() -> Path:
    """Create a demo C++ project."""
    temp_dir = Path(tempfile.mkdtemp(prefix="cpplib_reporter_"))
    src_dir = temp_dir / "src"
    src_dir.mkdir()

    # Create a string utilities library
    string_h = src_dir / "string_utils.h"
    string_h.write_text("""#ifndef STRING_UTILS_H
#define STRING_UTILS_H

#include <string>
#include <vector>

class StringUtil {
public:
    static std::string toUpperCase(const std::string& str);
    static std::string toLowerCase(const std::string& str);
    static std::vector<std::string> split(const std::string& str, char delimiter);
    static std::string trim(const std::string& str);
};

bool isNumeric(const std::string& str);
std::string reverse(const std::string& str);

#endif
""")

    # Implementation
    string_cpp = src_dir / "string_utils.cpp"
    string_cpp.write_text("""#include "string_utils.h"
#include <algorithm>
#include <cctype>
#include <sstream>

std::string StringUtil::toUpperCase(const std::string& str) {
    std::string result = str;
    std::transform(result.begin(), result.end(), result.begin(), ::toupper);
    return result;
}

std::string StringUtil::toLowerCase(const std::string& str) {
    std::string result = str;
    std::transform(result.begin(), result.end(), result.begin(), ::tolower);
    return result;
}

std::vector<std::string> StringUtil::split(const std::string& str, char delim) {
    std::vector<std::string> result;
    std::stringstream ss(str);
    std::string item;
    while (std::getline(ss, item, delim)) {
        result.push_back(item);
    }
    return result;
}

std::string StringUtil::trim(const std::string& str) {
    size_t start = str.find_first_not_of(" \\t\\n\\r");
    return (start == std::string::npos) ? "" : str.substr(start);
}

bool isNumeric(const std::string& str) {
    for (char c : str) {
        if (!std::isdigit(c)) return false;
    }
    return !str.empty();
}

std::string reverse(const std::string& str) {
    return std::string(str.rbegin(), str.rend());
}
""")

    # Main
    main_cpp = src_dir / "main.cpp"
    main_cpp.write_text("""#include <iostream>
#include "string_utils.h"

int main() {
    std::string test = "Hello World";
    std::cout << "Original: " << test << std::endl;
    std::cout << "Upper: " << StringUtil::toUpperCase(test) << std::endl;
    std::cout << "Lower: " << StringUtil::toLowerCase(test) << std::endl;
    std::cout << "Reversed: " << reverse(test) << std::endl;
    return 0;
}
""")

    # CMakeLists.txt
    cmake_file = temp_dir / "CMakeLists.txt"
    cmake_file.write_text("""cmake_minimum_required(VERSION 3.10)
project(string_utils)

set(CMAKE_CXX_STANDARD 17)

add_executable(demo src/main.cpp src/string_utils.cpp)
target_include_directories(demo PRIVATE src)
""")

    return temp_dir


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def main():
    """Run the reporter demo."""
    print("\n" + "="*70)
    print("  cpplib - Text Reporter Demo")
    print("="*70)
    print("\nDemonstrates compact text output for analysis results")

    # Create demo project
    print("\n📁 Creating demo project...")
    project_dir = create_demo_project()
    print(f"   Created at: {project_dir}\n")

    try:
        # Analyze the project
        print("📊 Analyzing codebase...")
        codebase = CPPCodebase(str(project_dir / "src"), str(project_dir))
        print("   ✓ Analysis complete\n")

        # Test 1: Compact text reporter
        print_section("Test 1: Compact Text Reporter")
        reporter = TextReporter(compact=True)

        functions = codebase.get_all_functions()
        classes = codebase.get_all_classes()
        files = codebase.file_index.list_files()

        print(reporter.codebase_statistics(len(functions), len(classes), len(files)))
        print()
        print(reporter.symbols_summary(functions, classes))

        # Test 2: Expanded text reporter
        print_section("Test 2: Expanded Text Reporter")
        reporter_expanded = TextReporter(compact=False)

        print(reporter_expanded.codebase_statistics(len(functions), len(classes), len(files)))
        print()
        print(reporter_expanded.symbols_summary(functions, classes))

        # Test 3: Code piece summary (compact)
        print_section("Test 3: Code Piece Summary (Compact)")
        piece = codebase.extract_function("toUpperCase")
        if piece:
            print(reporter.code_piece_summary(piece))
        else:
            print("(function not found)")

        # Test 4: Code piece summary (expanded)
        print_section("Test 4: Code Piece Summary (Expanded)")
        if piece:
            print(reporter_expanded.code_piece_summary(piece))

        # Test 5: Dependency report
        print_section("Test 5: Dependency Report")
        deps = codebase.get_dependencies("toUpperCase")
        print(reporter.dependency_report("toUpperCase", deps))

        # Test 6: Validation result
        print_section("Test 6: Validation Result Report")
        success, msg = codebase.validate()
        print(reporter.validation_result(success, msg))

        # Test 7: High-level analysis reporter
        print_section("Test 7: Full Analysis Report (using AnalysisReporter)")
        analysis_reporter = AnalysisReporter(codebase, compact=True)
        print(analysis_reporter.full_analysis())

        # Test 8: Symbol details
        print_section("Test 8: Symbol Details Report")
        print(analysis_reporter.symbol_details("reverse"))

        # Test 9: Extraction summary
        print_section("Test 9: Extraction Summary")
        pieces = [
            codebase.extract_function("toUpperCase"),
            codebase.extract_function("isNumeric"),
            codebase.extract_class("StringUtil"),
        ]
        pieces = [p for p in pieces if p is not None]
        print(reporter.extraction_summary(pieces))

        # Test 10: Comparison table
        print_section("Test 10: Comparison Table")
        comparison_data = [
            {"Name": "toUpperCase", "Kind": "method", "Lines": "6"},
            {"Name": "toLowerCase", "Kind": "method", "Lines": "6"},
            {"Name": "split", "Kind": "method", "Lines": "8"},
            {"Name": "trim", "Kind": "method", "Lines": "4"},
        ]
        print(reporter.comparison_table(comparison_data, ["Name", "Kind", "Lines"]))

        print_section("Demo Complete!")
        print("✅ Text Reporter Features:\n")
        print("1. ✓ Compact vs. expanded output modes")
        print("2. ✓ Symbol summaries (functions, classes)")
        print("3. ✓ Code piece reports with dependencies")
        print("4. ✓ Validation result reporting")
        print("5. ✓ High-level analysis reports")
        print("6. ✓ Formatted comparison tables")
        print("7. ✓ Symbol detail pages")
        print()
        print("Usage:")
        print("  reporter = TextReporter(compact=True)")
        print("  print(reporter.symbols_summary(functions, classes))")
        print("  print(reporter.validation_result(success, msg))")
        print("  print(reporter.code_piece_summary(piece))")
        print()

    finally:
        print("🧹 Cleaning up...")
        shutil.rmtree(project_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
