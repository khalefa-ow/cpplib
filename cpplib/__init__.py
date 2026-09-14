"""
C++ Code Generation & Manipulation Library

A Python library for reading, analyzing, and modifying C++ code.
Supports functions, classes, and files with semantic understanding.
"""

__version__ = "0.1.0"
__author__ = "khalefaow"

from cpplib.codebase import CPPCodebase
from cpplib.pieces.code_piece import CodePiece

__all__ = ["CPPCodebase", "CodePiece"]
