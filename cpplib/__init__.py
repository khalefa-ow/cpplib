"""
C++ Code Generation & Manipulation Library

A Python library for reading, analyzing, and modifying C++ code.
Supports functions, classes, and files with semantic understanding.
"""

__version__ = "0.1.0"
__author__ = "khalefaow"

from cpplib.codebase import CPPCodebase
from cpplib.pieces.code_piece import CodePiece
from cpplib.reporter.text_reporter import TextReporter, AnalysisReporter
from cpplib.validator.cpp_standard import CppStandard, StandardConfig, CXX98, CXX11, CXX14, CXX17, CXX20, CXX23

__all__ = [
    "CPPCodebase",
    "CodePiece",
    "TextReporter",
    "AnalysisReporter",
    "CppStandard",
    "StandardConfig",
    "CXX98",
    "CXX11",
    "CXX14",
    "CXX17",
    "CXX20",
    "CXX23",
]
