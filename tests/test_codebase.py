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


def test_extract_function_returns_none_if_not_found(temp_project_dir):
    """Test extract_function returns None if function not found."""
    codebase = CPPCodebase(str(temp_project_dir))
    result = codebase.extract_function("NonexistentFunction")
    assert result is None


def test_extract_class_returns_none_if_not_found(temp_project_dir):
    """Test extract_class returns None if class not found."""
    codebase = CPPCodebase(str(temp_project_dir))
    result = codebase.extract_class("NonexistentClass")
    assert result is None
