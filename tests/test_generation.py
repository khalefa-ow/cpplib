"""Tests for code generation and modification."""

import pytest
from pathlib import Path
from cpplib.generator.code_generator import CodeGenerator
from cpplib.generator.modifier import FileModifier
from cpplib.pieces.code_piece import CodePiece
from cpplib import CPPCodebase


def test_code_generator_initialization():
    """Test CodeGenerator initialization."""
    gen = CodeGenerator()
    assert gen.indent == "    "


def test_generator_function_code():
    """Test generating function code."""
    gen = CodeGenerator()
    piece = CodePiece(
        name="add",
        kind="function",
        signature="int add(int a, int b)",
        body="{ return a + b; }",
        dependencies=[],
    )

    code = gen.generate_function(piece)
    assert "add" in code
    assert "return a + b" in code
    assert "{" in code and "}" in code


def test_generator_class_code():
    """Test generating class code."""
    gen = CodeGenerator()
    piece = CodePiece(
        name="Calculator",
        kind="class",
        signature="class Calculator",
        body="{ public: int value; };",
        dependencies=[],
    )

    code = gen.generate_class(piece)
    assert "Calculator" in code
    assert "public:" in code


def test_generator_with_dependencies():
    """Test generating code with dependencies and includes."""
    gen = CodeGenerator()
    piece = CodePiece(
        name="processVector",
        kind="function",
        signature="void processVector(std::vector<int>& data)",
        body="{ /* implementation */ }",
        dependencies=["std::vector", "std::cout"],
    )

    code = gen.generate_function(piece)
    # Should include vector header
    assert "#include" in code
    assert "vector" in code.lower()


def test_generator_header_guard():
    """Test generating header guards."""
    gen = CodeGenerator()
    opening, closing = gen.generate_header_guard("MyClass.h")

    assert "#ifndef" in opening
    assert "#define" in opening
    assert "#endif" in closing
    assert "MY_CLASS_H" in opening or "MYCLASS_H" in opening


def test_generator_namespace_wrapping():
    """Test wrapping code in a namespace."""
    gen = CodeGenerator()
    code = "void foo() { }"
    wrapped = gen.wrap_in_namespace(code, "MyNamespace")

    assert "namespace MyNamespace" in wrapped
    assert "foo" in wrapped


def test_file_modifier_initialization():
    """Test FileModifier initialization."""
    modifier = FileModifier()
    assert modifier.generator is not None
    assert len(modifier.modifications) == 0


def test_file_modifier_insert_function(tmp_path):
    """Test inserting a function into a file."""
    # Create a test file
    test_file = tmp_path / "test.cpp"
    test_file.write_text("int main() { return 0; }\n")

    modifier = FileModifier()
    piece = CodePiece(
        name="add",
        kind="function",
        signature="int add(int a, int b)",
        body="{ return a + b; }",
    )

    result = modifier.insert_function(test_file, piece)
    assert result is True
    assert len(modifier.modifications) == 1


def test_file_modifier_pending_changes(tmp_path):
    """Test getting pending changes summary."""
    test_file = tmp_path / "test.cpp"
    test_file.write_text("int main() { return 0; }\n")

    modifier = FileModifier()
    piece = CodePiece(
        name="add",
        kind="function",
        signature="int add(int a, int b)",
        body="{ return a + b; }",
    )

    modifier.insert_function(test_file, piece)
    changes = modifier.get_pending_changes()

    assert len(changes) == 1
    assert str(test_file) in changes[0][0]


def test_file_modifier_rollback(tmp_path):
    """Test rolling back modifications."""
    test_file = tmp_path / "test.cpp"
    test_file.write_text("int main() { return 0; }\n")

    modifier = FileModifier()
    piece = CodePiece(
        name="add",
        kind="function",
        signature="int add(int a, int b)",
        body="{ return a + b; }",
    )

    modifier.insert_function(test_file, piece)
    assert len(modifier.modifications) == 1

    modifier.rollback()
    assert len(modifier.modifications) == 0


def test_file_modifier_apply_modifications(tmp_path):
    """Test applying modifications to files."""
    test_file = tmp_path / "test.cpp"
    original_content = "int main() { return 0; }\n"
    test_file.write_text(original_content)

    modifier = FileModifier()
    piece = CodePiece(
        name="add",
        kind="function",
        signature="int add(int a, int b)",
        body="{ return a + b; }",
    )

    modifier.insert_function(test_file, piece)
    result = modifier.apply_modifications()

    assert result is True

    # Verify file was modified
    new_content = test_file.read_text()
    assert new_content != original_content
    assert "add" in new_content


def test_codebase_insert_and_generate(sample_cpp_files):
    """Test inserting functions and generating changes."""
    codebase = CPPCodebase(str(sample_cpp_files))

    # Create a new function piece
    piece = CodePiece(
        name="multiply",
        kind="function",
        signature="int multiply(int a, int b)",
        body="{ return a * b; }",
        dependencies=[],
    )

    # Find a C++ file to insert into
    cpp_files = list(Path(sample_cpp_files).glob("*.cpp"))
    if cpp_files:
        # Queue insertion
        result = codebase.insert_function(str(cpp_files[0]), piece)
        assert isinstance(result, bool)


def test_generator_code_formatting():
    """Test code formatting with indentation."""
    gen = CodeGenerator()
    code = "int main() {\nreturn 0;\n}"

    formatted = gen.format_code(code, indent_level=1)
    assert formatted.startswith("    ")  # Should have indent


def test_generator_merge_code_at_start():
    """Test merging code at the start of a file."""
    gen = CodeGenerator()
    original = "int main() { return 0; }\n"
    new_code = "void helper() { }\n"

    merged = gen.merge_code(original, new_code, position="start")
    assert merged.startswith(new_code)


def test_generator_merge_code_at_end():
    """Test merging code at the end of a file."""
    gen = CodeGenerator()
    original = "int main() { return 0; }\n"
    new_code = "void helper() { }\n"

    merged = gen.merge_code(original, new_code, position="end")
    assert merged.endswith(new_code)


def test_file_modifier_insert_class(tmp_path):
    """Test inserting a class into a file."""
    test_file = tmp_path / "test.cpp"
    test_file.write_text("namespace MyNamespace { }\n")

    modifier = FileModifier()
    piece = CodePiece(
        name="MyClass",
        kind="class",
        signature="class MyClass",
        body="{ public: int value; };",
    )

    result = modifier.insert_class(test_file, piece)
    assert result is True


def test_file_modifier_delete_function(tmp_path):
    """Test deleting a function from a file."""
    test_file = tmp_path / "test.cpp"
    content = "int add(int a, int b) { return a + b; }\nint main() { return 0; }\n"
    test_file.write_text(content)

    modifier = FileModifier()
    result = modifier.delete_function(test_file, "add")
    assert isinstance(result, bool)
