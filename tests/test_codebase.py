"""Tests for CPPCodebase class."""

import pytest
from pathlib import Path
from cpplib.codebase import CPPCodebase


def test_codebase_initialization(temp_project_dir):
    """Test CPPCodebase can be initialized with a valid directory."""
    codebase = CPPCodebase(str(temp_project_dir))
    assert codebase.root_dir == temp_project_dir
    assert codebase.cmake_dir == temp_project_dir


def test_codebase_initialization_with_cmake_dir(temp_project_dir):
    """Test CPPCodebase initialization with explicit cmake_dir."""
    cmake_dir = temp_project_dir / "build"
    codebase = CPPCodebase(str(temp_project_dir), cmake_dir=str(cmake_dir))
    assert codebase.cmake_dir == cmake_dir


def test_codebase_invalid_directory():
    """Test CPPCodebase raises error for invalid directory."""
    with pytest.raises(ValueError, match="Root directory does not exist"):
        CPPCodebase("/nonexistent/path")


def test_extract_function_not_implemented(temp_project_dir):
    """Test extract_function raises NotImplementedError."""
    codebase = CPPCodebase(str(temp_project_dir))
    with pytest.raises(NotImplementedError):
        codebase.extract_function("MyClass::myMethod")


def test_extract_class_not_implemented(temp_project_dir):
    """Test extract_class raises NotImplementedError."""
    codebase = CPPCodebase(str(temp_project_dir))
    with pytest.raises(NotImplementedError):
        codebase.extract_class("MyClass")
