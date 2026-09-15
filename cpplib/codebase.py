"""Main CPPCodebase class for managing C++ projects."""

from pathlib import Path
from typing import Optional, List
from cpplib.parser.cpp_parser import CPPParser
from cpplib.parser.file_index import FileIndex
from cpplib.semantic.analyzer import SemanticAnalyzer
from cpplib.semantic.scope_resolver import Symbol


class CPPCodebase:
    """
    Main interface for analyzing and modifying C++ codebases.

    Handles parsing, semantic analysis, code extraction, and modification.
    """

    def __init__(self, root_dir: str, cmake_dir: Optional[str] = None):
        """
        Initialize a C++ codebase.

        Args:
            root_dir: Root directory of the C++ project
            cmake_dir: Path to CMakeLists.txt (defaults to root_dir)
        """
        self.root_dir = Path(root_dir)
        self.cmake_dir = Path(cmake_dir) if cmake_dir else self.root_dir

        if not self.root_dir.exists():
            raise ValueError(f"Root directory does not exist: {root_dir}")

        self.parser = CPPParser()
        self.file_index = FileIndex(self.root_dir)
        self.file_index.index_directory()

        self.semantic_analyzer = SemanticAnalyzer(self.file_index)
        self.semantic_analyzer.analyze()

    def extract_function(self, qualified_name: str):
        """Extract a function/method with its dependencies."""
        raise NotImplementedError("Function extraction not yet implemented")

    def extract_class(self, qualified_name: str):
        """Extract a class with its dependencies."""
        raise NotImplementedError("Class extraction not yet implemented")

    def insert_function(self, file_path: str, piece, after: Optional[str] = None, before: Optional[str] = None):
        """Insert a code piece (function) into a file."""
        raise NotImplementedError("Generator layer not yet implemented")

    def insert_class(self, file_path: str, piece, after: Optional[str] = None, before: Optional[str] = None):
        """Insert a code piece (class) into a file."""
        raise NotImplementedError("Generator layer not yet implemented")

    def generate(self):
        """Generate changes (apply modifications to files)."""
        raise NotImplementedError("Generator layer not yet implemented")

    def validate(self) -> bool:
        """Validate the codebase compiles correctly using cmake."""
        raise NotImplementedError("Validator layer not yet implemented")

    def get_all_functions(self) -> List[Symbol]:
        """Get all functions in the codebase."""
        return self.semantic_analyzer.list_all_functions()

    def get_all_classes(self) -> List[Symbol]:
        """Get all classes/structs in the codebase."""
        return self.semantic_analyzer.list_all_classes()

    def get_symbol(self, name: str) -> Optional[Symbol]:
        """Look up a symbol by name."""
        return self.semantic_analyzer.get_symbol(name)

    def get_dependencies(self, symbol_name: str) -> dict:
        """Get dependencies for a symbol."""
        return self.semantic_analyzer.get_dependencies(symbol_name)
