"""Tests for CodePiece class."""

import pytest
from cpplib.pieces.code_piece import CodePiece


def test_code_piece_creation():
    """Test CodePiece can be created with basic properties."""
    piece = CodePiece(
        name="myFunction",
        kind="function",
        signature="void myFunction(int x)",
        body="{ x++; }",
    )
    assert piece.name == "myFunction"
    assert piece.kind == "function"
    assert piece.signature == "void myFunction(int x)"
    assert piece.body == "{ x++; }"


def test_code_piece_with_dependencies():
    """Test CodePiece with dependencies."""
    piece = CodePiece(
        name="myFunction",
        kind="function",
        signature="void myFunction()",
        body="{}",
        dependencies=["#include <iostream>", "std::cout"],
    )
    assert piece.get_dependencies() == {"#include <iostream>", "std::cout"}


def test_code_piece_with_location():
    """Test CodePiece with source location."""
    piece = CodePiece(
        name="myFunction",
        kind="function",
        signature="void myFunction()",
        body="{}",
        source_file="main.cpp",
        line_start=10,
        line_end=12,
    )
    assert piece.source_file == "main.cpp"
    assert piece.line_start == 10
    assert piece.line_end == 12


def test_code_piece_str_representation():
    """Test CodePiece string representation."""
    piece = CodePiece(
        name="myFunction",
        kind="function",
        signature="void myFunction()",
        body="{}",
        source_file="main.cpp",
        line_start=10,
    )
    assert "myFunction" in str(piece)
    assert "main.cpp" in str(piece)
