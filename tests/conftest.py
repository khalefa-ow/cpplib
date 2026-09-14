"""Pytest configuration and fixtures."""

import pytest
from pathlib import Path
import tempfile
import shutil


@pytest.fixture
def temp_project_dir():
    """Create a temporary project directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_cpp_files(temp_project_dir):
    """Create sample C++ files for testing."""
    # Create src directory
    src_dir = temp_project_dir / "src"
    src_dir.mkdir()

    # Create a simple C++ file
    main_cpp = src_dir / "main.cpp"
    main_cpp.write_text("""#include <iostream>
#include "utils.h"

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
""")

    # Create a header file
    utils_h = src_dir / "utils.h"
    utils_h.write_text("""#ifndef UTILS_H
#define UTILS_H

void printMessage(const char* msg);

#endif  // UTILS_H
""")

    # Create implementation
    utils_cpp = src_dir / "utils.cpp"
    utils_cpp.write_text("""#include "utils.h"
#include <iostream>

void printMessage(const char* msg) {
    std::cout << msg << std::endl;
}
""")

    return src_dir
