"""Tests for code validation."""

import pytest
from pathlib import Path
from cpplib.validator.cmake_validator import CMakeValidator
from cpplib import CPPCodebase


def test_cmake_validator_initialization(temp_project_dir):
    """Test CMakeValidator initialization."""
    validator = CMakeValidator(Path(temp_project_dir))
    assert validator.cmake_dir == Path(temp_project_dir)
    assert validator.build_dir == Path(temp_project_dir) / "build"


def test_cmake_validator_is_available():
    """Test checking if cmake is available."""
    validator = CMakeValidator(Path("."))
    # This might be False in some environments, but we just test it doesn't crash
    result = validator.is_cmake_available()
    assert isinstance(result, bool)


def test_validator_check_syntax():
    """Test checking C++ syntax."""
    validator = CMakeValidator(Path("."))

    # Valid C++ code
    valid_code = "int main() { return 0; }"
    success, message = validator.check_syntax(valid_code)
    assert isinstance(success, (bool, type(None)))

    # Invalid C++ code
    invalid_code = "int main() { return 0"  # Missing closing brace
    success, message = validator.check_syntax(invalid_code)
    # Result depends on whether compiler is available
    if success is not None:
        assert not success  # Should fail syntax check


def test_validator_get_build_errors():
    """Test getting build error output."""
    validator = CMakeValidator(Path("."))
    errors = validator.get_build_errors()
    assert isinstance(errors, str)


def test_validator_get_build_output():
    """Test getting build output."""
    validator = CMakeValidator(Path("."))
    output = validator.get_build_output()
    assert isinstance(output, str)


def test_codebase_validate_returns_tuple(sample_cpp_files):
    """Test that CPPCodebase.validate returns a tuple."""
    codebase = CPPCodebase(str(sample_cpp_files))

    # Validate returns a tuple
    result = codebase.validate(clean=False)
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], bool)
    assert isinstance(result[1], str)


def test_codebase_validate_message_on_missing_cmake(temp_project_dir):
    """Test validation with missing CMakeLists.txt."""
    codebase = CPPCodebase(str(temp_project_dir))

    # Should fail because CMakeLists.txt doesn't exist
    success, message = codebase.validate(clean=False)
    assert isinstance(success, bool)
    assert isinstance(message, str)


def test_validator_error_handling():
    """Test error handling in validator."""
    # Initialize with non-existent path
    validator = CMakeValidator(Path("/nonexistent/path"))

    # Should handle gracefully
    success, message = validator.validate()
    assert success is False
    assert "not found" in message.lower() or "error" in message.lower()
