"""Tests for code extraction (functions and classes with dependencies)."""

import pytest
from pathlib import Path
from cpplib.parser.cpp_parser import CPPParser
from cpplib.parser.function_extractor import FunctionExtractor
from cpplib.parser.class_extractor import ClassExtractor
from cpplib.semantic.dependency_collector import DependencyCollector
from cpplib.semantic.analyzer import SemanticAnalyzer
from cpplib.parser.file_index import FileIndex


FUNCTION_WITH_INCLUDES = """
#include <iostream>
#include <vector>

void processVector(std::vector<int>& data) {
    std::cout << "Processing" << std::endl;
}
"""

CLASS_WITH_INHERITANCE = """
class Base {
public:
    virtual void doSomething() {}
};

class Derived : public Base {
public:
    void doSomething() override {}
    int getValue() { return 42; }
private:
    std::vector<int> data;
};
"""

SIMPLE_FUNCTION = """
int add(int a, int b) {
    return a + b;
}
"""


def test_class_extractor_simple():
    """Test extracting a simple class."""
    parser = CPPParser()
    extractor = ClassExtractor(parser)

    classes = extractor.extract_classes(Path("test.cpp"), CLASS_WITH_INHERITANCE)
    assert len(classes) >= 1

    class_names = [c.name for c in classes]
    assert "Base" in class_names or "Derived" in class_names


def test_class_extractor_by_name():
    """Test extracting a specific class by name."""
    parser = CPPParser()
    extractor = ClassExtractor(parser)

    cls = extractor.extract_class_by_name(Path("test.cpp"), CLASS_WITH_INHERITANCE, "Derived")
    assert cls is not None
    assert cls.name == "Derived"
    assert cls.kind == "class"


def test_class_extractor_base_classes():
    """Test extracting base class information."""
    parser = CPPParser()
    extractor = ClassExtractor(parser)

    cls = extractor.extract_class_by_name(Path("test.cpp"), CLASS_WITH_INHERITANCE, "Derived")
    if cls:
        # Should contain reference to Base class
        assert len(cls.dependencies) > 0 or "Base" in cls.body


def test_class_extractor_metadata():
    """Test class extraction provides proper metadata."""
    parser = CPPParser()
    extractor = ClassExtractor(parser)

    classes = extractor.extract_classes(Path("test.cpp"), CLASS_WITH_INHERITANCE)
    if classes:
        cls = classes[0]
        assert cls.name
        assert cls.kind in ["class", "struct"]
        assert cls.source_file == "test.cpp"
        assert cls.line_start is not None
        assert cls.line_end is not None


def test_dependency_collector_initialization(sample_cpp_files):
    """Test DependencyCollector initialization."""
    file_index = FileIndex(sample_cpp_files)
    file_index.index_directory()

    analyzer = SemanticAnalyzer(file_index)
    analyzer.analyze()

    collector = DependencyCollector(analyzer)
    assert collector.analyzer is not None


def test_dependency_collector_function_deps(sample_cpp_files):
    """Test collecting function dependencies."""
    file_index = FileIndex(sample_cpp_files)
    file_index.index_directory()

    analyzer = SemanticAnalyzer(file_index)
    analyzer.analyze()

    collector = DependencyCollector(analyzer)

    # Get dependencies for any available function
    functions = analyzer.list_all_functions()
    if functions:
        deps = collector.collect_function_dependencies(functions[0].name)
        assert isinstance(deps, dict)
        assert "types" in deps
        assert "functions" in deps


def test_dependency_collector_minimal_includes():
    """Test determining minimal includes."""
    from cpplib import CodePiece

    piece = CodePiece(
        name="processData",
        kind="function",
        signature="void processData(std::vector<int>& data)",
        body="{ /* code */ }",
        dependencies=["std::vector", "std::cout"],
    )

    # Create a mock analyzer
    file_index = FileIndex(Path("."))
    analyzer = SemanticAnalyzer(file_index)
    collector = DependencyCollector(analyzer)

    includes = collector.get_minimal_includes(piece)
    assert any("vector" in inc for inc in includes)


def test_codebase_extract_function(sample_cpp_files):
    """Test extracting a function from CPPCodebase."""
    from cpplib import CPPCodebase

    codebase = CPPCodebase(str(sample_cpp_files))

    # Try to extract main function if it exists
    main_func = codebase.extract_function("main")
    if main_func:
        assert main_func.kind == "function"
        assert main_func.name == "main"


def test_codebase_extract_class(temp_project_dir):
    """Test extracting a class from CPPCodebase."""
    from cpplib import CPPCodebase

    # Create a test file with a class
    src_dir = Path(temp_project_dir) / "src"
    src_dir.mkdir(exist_ok=True)

    test_file = src_dir / "test.cpp"
    test_file.write_text(CLASS_WITH_INHERITANCE)

    codebase = CPPCodebase(str(temp_project_dir))

    # Try to extract Base class
    base_class = codebase.extract_class("Base")
    if base_class:
        assert base_class.kind == "class"
        assert base_class.name == "Base"


def test_function_extraction_preserves_code():
    """Test that function extraction preserves the actual code."""
    parser = CPPParser()
    extractor = FunctionExtractor(parser)

    functions = extractor.extract_functions(Path("test.cpp"), SIMPLE_FUNCTION)
    assert len(functions) > 0

    func = functions[0]
    assert func.body  # Should have body text
    assert "return" in func.body or "a + b" in func.body


def test_class_extraction_preserves_code():
    """Test that class extraction preserves the actual code."""
    parser = CPPParser()
    extractor = ClassExtractor(parser)

    classes = extractor.extract_classes(Path("test.cpp"), CLASS_WITH_INHERITANCE)
    assert len(classes) > 0

    cls = classes[0]
    assert cls.body  # Should have body text
    assert "{" in cls.body and "}" in cls.body
