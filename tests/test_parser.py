"""Tests for the C++ parser layer."""

import pytest
from pathlib import Path
from cpplib.parser.cpp_parser import CPPParser
from cpplib.parser.file_index import FileIndex
from cpplib.parser.function_extractor import FunctionExtractor


SIMPLE_CPP = """
#include <iostream>

int add(int a, int b) {
    return a + b;
}

void printMessage(const char* msg) {
    std::cout << msg << std::endl;
}
"""

CLASS_CPP = """
class Calculator {
public:
    int add(int a, int b) {
        return a + b;
    }

    int subtract(int a, int b) {
        return a - b;
    }
};
"""


def test_parser_initialization():
    """Test CPPParser can be initialized."""
    parser = CPPParser()
    assert parser.language is not None
    assert parser.parser is not None


def test_parse_simple_cpp():
    """Test parsing simple C++ code."""
    parser = CPPParser()
    root = parser.parse_string(SIMPLE_CPP)
    assert root is not None
    assert root.type == "translation_unit"


def test_find_functions():
    """Test finding all functions in code."""
    parser = CPPParser()
    root = parser.parse_string(SIMPLE_CPP)
    functions = parser.find_functions(root)
    assert len(functions) >= 2  # At least add and printMessage


def test_find_includes():
    """Test extracting includes from code."""
    parser = CPPParser()
    root = parser.parse_string(SIMPLE_CPP)
    includes = parser.find_includes(root)
    assert len(includes) > 0
    assert any("iostream" in inc for inc in includes)


def test_function_extractor_simple():
    """Test extracting functions with FunctionExtractor."""
    parser = CPPParser()
    extractor = FunctionExtractor(parser)

    functions = extractor.extract_functions(Path("test.cpp"), SIMPLE_CPP)
    assert len(functions) >= 2

    # Check first function
    func_names = [f.name for f in functions]
    assert "add" in func_names
    assert "printMessage" in func_names


def test_function_extractor_by_name():
    """Test extracting a specific function by name."""
    parser = CPPParser()
    extractor = FunctionExtractor(parser)

    func = extractor.extract_function_by_name(Path("test.cpp"), SIMPLE_CPP, "add")
    assert func is not None
    assert func.name == "add"
    assert func.kind == "function"
    assert "int" in func.signature or "int" in func.body


def test_file_index_initialization(temp_project_dir):
    """Test FileIndex can be initialized."""
    index = FileIndex(temp_project_dir)
    assert index.root_dir == temp_project_dir


def test_file_index_index_directory(sample_cpp_files):
    """Test indexing a directory."""
    index = FileIndex(sample_cpp_files)
    index.index_directory()

    files = index.list_files()
    assert len(files) > 0

    # Check that our sample files were indexed
    file_names = [f.name for f in files]
    assert "main.cpp" in file_names or any("cpp" in name for name in file_names)


def test_file_index_get_ast(sample_cpp_files):
    """Test retrieving AST from index."""
    index = FileIndex(sample_cpp_files)
    index.index_directory()

    files = index.list_files()
    if files:
        ast = index.get_ast(files[0])
        assert ast is not None


def test_function_piece_metadata():
    """Test that extracted functions have proper metadata."""
    parser = CPPParser()
    extractor = FunctionExtractor(parser)

    functions = extractor.extract_functions(Path("test.cpp"), SIMPLE_CPP)
    func = functions[0]

    assert func.name
    assert func.kind == "function"
    assert func.signature
    assert func.body
    assert func.source_file == "test.cpp"
    assert func.line_start is not None
    assert func.line_end is not None
    assert func.line_start <= func.line_end
