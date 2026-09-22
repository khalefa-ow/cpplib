"""Tree-sitter C++ parser wrapper."""

from pathlib import Path
from typing import List, Optional
from tree_sitter import Parser, Language, Node
from tree_sitter_cpp import language as cpp_language


class CPPParser:
    """Wrapper around tree-sitter for C++ parsing."""

    def __init__(self):
        """Initialize the C++ parser."""
        self.language = Language(cpp_language())
        self.parser = Parser()
        self.parser.language = self.language

    def parse_file(self, file_path: Path) -> Optional[Node]:
        """
        Parse a C++ file and return the AST root node.

        Args:
            file_path: Path to the C++ file

        Returns:
            Root node of the AST, or None if parsing fails
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            code = f.read()

        try:
            tree = self.parser.parse(code.encode("utf-8"))
            return tree.root_node
        except Exception as e:
            print(f"Failed to parse {file_path}: {e}")
            return None

    def parse_string(self, code: str) -> Optional[Node]:
        """
        Parse C++ code from a string and return the AST root node.

        Args:
            code: C++ code as string

        Returns:
            Root node of the AST, or None if parsing fails
        """
        try:
            tree = self.parser.parse(code.encode("utf-8"))
            return tree.root_node
        except Exception as e:
            print(f"Failed to parse code: {e}")
            return None

    def find_functions(self, node: Node) -> List[Node]:
        """Find all function declarations and definitions in an AST."""
        functions = []
        self._find_nodes_by_type(node, ["function_definition", "declaration"], functions)
        return functions

    def find_classes(self, node: Node) -> List[Node]:
        """Find all class/struct definitions in an AST."""
        classes = []
        self._find_nodes_by_type(node, ["class_specifier", "struct_specifier"], classes)
        return classes

    def find_includes(self, node: Node) -> List[str]:
        """Extract all #include directives from an AST."""
        includes = []
        self._collect_includes(node, includes)
        return includes

    def get_node_text(self, node: Node, code: str) -> str:
        """Get the source text for a node."""
        start = node.start_byte
        end = node.end_byte
        return code[start:end]

    def get_node_line_range(self, node: Node) -> tuple[int, int]:
        """Get the line range (start, end) for a node."""
        return (node.start_point[0] + 1, node.end_point[0] + 1)

    def _find_nodes_by_type(self, node: Node, types: List[str], result: List[Node]) -> None:
        """Recursively find all nodes of given types."""
        if node.type in types:
            result.append(node)

        for child in node.children:
            self._find_nodes_by_type(child, types, result)

    def _collect_includes(self, node: Node, includes: List[str]) -> None:
        """Recursively collect all include directives."""
        if node.type == "preproc_include":
            # Extract include path from the include directive
            include_text = node.text.decode("utf-8") if isinstance(node.text, bytes) else node.text
            includes.append(include_text)

        for child in node.children:
            self._collect_includes(child, includes)
