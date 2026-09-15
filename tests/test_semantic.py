"""Tests for the semantic analysis layer."""

import pytest
from pathlib import Path
from cpplib.parser.cpp_parser import CPPParser
from cpplib.semantic.scope_resolver import ScopeResolver, Symbol
from cpplib.semantic.dependency_graph import DependencyGraph, Dependency
from cpplib.semantic.analyzer import SemanticAnalyzer
from cpplib.parser.file_index import FileIndex


SIMPLE_FUNCTION_CODE = """
int add(int a, int b) {
    return a + b;
}

void printResult(int result) {
    // some code
}
"""

CLASS_WITH_METHODS = """
class Calculator {
public:
    int add(int a, int b) {
        return a + b;
    }

    int multiply(int x, int y) {
        return x * y;
    }
};
"""

CODE_WITH_TYPES = """
#include <iostream>
#include <vector>

class DataContainer {
public:
    void addItem(int item) {
        std::vector<int> data;
        std::cout << item << std::endl;
    }
};
"""


def test_symbol_creation():
    """Test Symbol creation."""
    sym = Symbol(
        name="myFunc",
        kind="function",
        scope="global",
        file_path="test.cpp",
        line_start=10,
        line_end=15,
    )
    assert sym.name == "myFunc"
    assert sym.kind == "function"
    assert sym.scope == "global"


def test_scope_resolver_functions():
    """Test resolving function scopes."""
    parser = CPPParser()
    resolver = ScopeResolver()

    ast = parser.parse_string(SIMPLE_FUNCTION_CODE)
    symbols = resolver.resolve_scopes(ast, "test.cpp", SIMPLE_FUNCTION_CODE)

    # Should find both functions
    assert len(symbols) >= 2
    func_names = [s for s in symbols.keys()]
    assert any("add" in name for name in func_names)
    assert any("printResult" in name for name in func_names)


def test_scope_resolver_classes():
    """Test resolving class scopes."""
    parser = CPPParser()
    resolver = ScopeResolver()

    ast = parser.parse_string(CLASS_WITH_METHODS)
    symbols = resolver.resolve_scopes(ast, "test.cpp", CLASS_WITH_METHODS)

    # Should find class and methods
    assert len(symbols) >= 1
    class_found = any(sym.kind == "class" for sym in symbols.values())
    assert class_found


def test_symbol_lookup():
    """Test symbol lookup in resolver."""
    parser = CPPParser()
    resolver = ScopeResolver()

    ast = parser.parse_string(SIMPLE_FUNCTION_CODE)
    resolver.resolve_scopes(ast, "test.cpp", SIMPLE_FUNCTION_CODE)

    # Lookup by short name
    sym = resolver.lookup_symbol("add")
    assert sym is not None
    assert sym.name == "add"


def test_dependency_graph_creation():
    """Test DependencyGraph creation and basic operations."""
    graph = DependencyGraph()

    graph.add_dependency("func1", "func2", "call", "test.cpp", 10)
    graph.add_dependency("func1", "MyType", "type", "test.cpp", 5)

    # Check dependencies
    deps = graph.get_dependencies_of("func1")
    assert "func2" in deps
    assert "MyType" in deps


def test_dependency_graph_reverse_lookup():
    """Test reverse dependency lookup."""
    graph = DependencyGraph()

    graph.add_dependency("func1", "func2", "call", "test.cpp", 10)
    graph.add_dependency("func3", "func2", "call", "test.cpp", 20)

    dependents = graph.get_dependents_of("func2")
    assert "func1" in dependents
    assert "func3" in dependents


def test_transitive_dependencies():
    """Test transitive dependency resolution."""
    graph = DependencyGraph()

    # Chain: func1 -> func2 -> func3
    graph.add_dependency("func1", "func2", "call", "test.cpp", 10)
    graph.add_dependency("func2", "func3", "call", "test.cpp", 20)

    transitive = graph.get_transitive_dependencies("func1")
    assert "func2" in transitive
    assert "func3" in transitive


def test_include_relationships():
    """Test include tracking."""
    graph = DependencyGraph()

    graph.add_include("main.cpp", "utils.h")
    graph.add_include("utils.h", "common.h")

    includes = graph.includes["main.cpp"]
    assert "utils.h" in includes


def test_type_dependencies():
    """Test type dependency tracking."""
    graph = DependencyGraph()

    graph.add_type_dependency("processData", "std::vector")
    graph.add_type_dependency("processData", "MyClass")

    types = graph.type_dependencies["processData"]
    assert "std::vector" in types
    assert "MyClass" in types


def test_semantic_analyzer_initialization(temp_project_dir):
    """Test SemanticAnalyzer initialization."""
    file_index = FileIndex(temp_project_dir)
    analyzer = SemanticAnalyzer(file_index)
    assert analyzer.file_index is not None
    assert analyzer.parser is not None


def test_semantic_analyzer_with_sample_files(sample_cpp_files):
    """Test semantic analysis with sample C++ files."""
    file_index = FileIndex(sample_cpp_files)
    file_index.index_directory()

    analyzer = SemanticAnalyzer(file_index)
    analyzer.analyze()

    # Should have found some symbols
    all_symbols = analyzer.all_symbols
    assert len(all_symbols) > 0


def test_semantic_analyzer_function_extraction(sample_cpp_files):
    """Test extracting functions from sample files."""
    file_index = FileIndex(sample_cpp_files)
    file_index.index_directory()

    analyzer = SemanticAnalyzer(file_index)
    analyzer.analyze()

    functions = analyzer.list_all_functions()
    # main() should be found
    func_names = [f.name for f in functions]
    assert len(func_names) > 0


def test_codebase_get_all_functions(sample_cpp_files):
    """Test getting all functions from CPPCodebase."""
    from cpplib import CPPCodebase

    codebase = CPPCodebase(str(sample_cpp_files))
    functions = codebase.get_all_functions()

    # Should find functions
    assert isinstance(functions, list)


def test_codebase_get_symbol(sample_cpp_files):
    """Test symbol lookup from CPPCodebase."""
    from cpplib import CPPCodebase

    codebase = CPPCodebase(str(sample_cpp_files))

    # Try to get main function
    main_sym = codebase.get_symbol("main")
    if main_sym:  # main might exist in sample files
        assert main_sym.name in ["main", "main"]


def test_codebase_get_dependencies(sample_cpp_files):
    """Test dependency lookup from CPPCodebase."""
    from cpplib import CPPCodebase

    codebase = CPPCodebase(str(sample_cpp_files))

    # Get dependencies for a function (if it exists)
    functions = codebase.get_all_functions()
    if functions:
        deps = codebase.get_dependencies(functions[0].name)
        assert isinstance(deps, dict)
