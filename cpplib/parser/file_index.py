"""File indexing for C++ codebases."""

from pathlib import Path
from typing import Dict, List, Optional
from tree_sitter import Node
from cpplib.parser.cpp_parser import CPPParser


class FileIndex:
    """Index of C++ files in a codebase."""

    def __init__(self, root_dir: Path):
        """
        Initialize the file index for a codebase.

        Args:
            root_dir: Root directory of the C++ project
        """
        self.root_dir = Path(root_dir)
        self.parser = CPPParser()
        self.files: Dict[Path, Optional[Node]] = {}
        self.file_contents: Dict[Path, str] = {}

    def index_directory(self) -> None:
        """Scan and index all C++ files in the root directory."""
        cpp_extensions = {".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx"}

        for file_path in self.root_dir.rglob("*"):
            if file_path.is_file() and file_path.suffix in cpp_extensions:
                self._index_file(file_path)

    def _index_file(self, file_path: Path) -> None:
        """Index a single C++ file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.file_contents[file_path] = content
            ast = self.parser.parse_string(content)
            self.files[file_path] = ast
        except Exception as e:
            print(f"Failed to index {file_path}: {e}")
            self.files[file_path] = None

    def get_ast(self, file_path: Path) -> Optional[Node]:
        """Get the AST for a file (indexes if needed)."""
        if file_path not in self.files:
            self._index_file(file_path)
        return self.files.get(file_path)

    def get_content(self, file_path: Path) -> Optional[str]:
        """Get the file content."""
        if file_path not in self.file_contents:
            self._index_file(file_path)
        return self.file_contents.get(file_path)

    def list_files(self) -> List[Path]:
        """List all indexed files."""
        return list(self.files.keys())
