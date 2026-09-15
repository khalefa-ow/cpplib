"""Extract functions and their metadata from C++ AST."""

from pathlib import Path
from typing import Optional, List, Dict, Any
from tree_sitter import Node
from cpplib.pieces.code_piece import CodePiece
from cpplib.parser.cpp_parser import CPPParser


class FunctionExtractor:
    """Extract function definitions and metadata from C++ code."""

    def __init__(self, parser: CPPParser):
        """
        Initialize the function extractor.

        Args:
            parser: CPPParser instance
        """
        self.parser = parser

    def extract_functions(self, file_path: Path, code: str) -> List[CodePiece]:
        """
        Extract all functions from a C++ file.

        Args:
            file_path: Path to the C++ file
            code: Source code as string

        Returns:
            List of CodePiece objects representing functions
        """
        ast = self.parser.parse_string(code)
        if not ast:
            return []

        functions = []
        self._collect_functions(ast, file_path, code, functions)
        return functions

    def extract_function_by_name(
        self, file_path: Path, code: str, func_name: str
    ) -> Optional[CodePiece]:
        """
        Extract a specific function by name.

        Args:
            file_path: Path to the C++ file
            code: Source code as string
            func_name: Name of the function to extract

        Returns:
            CodePiece representing the function, or None if not found
        """
        functions = self.extract_functions(file_path, code)
        for func in functions:
            if func.name == func_name:
                return func
        return None

    def _collect_functions(
        self, node: Node, file_path: Path, code: str, result: List[CodePiece]
    ) -> None:
        """Recursively collect function definitions."""
        if node.type == "function_definition":
            piece = self._parse_function_definition(node, file_path, code)
            if piece:
                result.append(piece)

        for child in node.children:
            self._collect_functions(child, file_path, code, result)

    def _parse_function_definition(
        self, node: Node, file_path: Path, code: str
    ) -> Optional[CodePiece]:
        """Parse a function_definition node into a CodePiece."""
        try:
            # Find function name (usually in declarator or function_declarator)
            func_name = self._extract_function_name(node)
            if not func_name:
                return None

            # Get signature (from start to opening brace)
            signature = self._extract_signature(node, code)

            # Get body (the compound statement)
            body = self._extract_body(node, code)

            # Get line numbers
            start_line, end_line = self.parser.get_node_line_range(node)

            # Extract includes used
            dependencies = self._extract_dependencies(node, code)

            return CodePiece(
                name=func_name,
                kind="function",
                signature=signature,
                body=body,
                source_file=str(file_path),
                line_start=start_line,
                line_end=end_line,
                dependencies=dependencies,
            )
        except Exception as e:
            print(f"Failed to parse function definition: {e}")
            return None

    def _extract_function_name(self, node: Node) -> Optional[str]:
        """Extract function name from function_definition node."""
        for child in node.children:
            if child.type in ["declarator", "function_declarator", "pointer_declarator"]:
                # Recursively find the identifier
                name = self._find_identifier(child)
                if name:
                    return name
        return None

    def _find_identifier(self, node: Node) -> Optional[str]:
        """Find the first identifier node in the tree."""
        if node.type == "identifier":
            return node.text.decode("utf-8") if isinstance(node.text, bytes) else node.text

        for child in node.children:
            result = self._find_identifier(child)
            if result:
                return result
        return None

    def _extract_signature(self, node: Node, code: str) -> str:
        """Extract function signature up to opening brace."""
        start = node.start_byte
        # Find the opening brace or compound statement
        for child in node.children:
            if child.type == "compound_statement":
                end = child.start_byte
                return code[start:end].strip()
        # Fallback to full node text
        return code[start : node.end_byte].strip()

    def _extract_body(self, node: Node, code: str) -> str:
        """Extract function body (compound statement)."""
        for child in node.children:
            if child.type == "compound_statement":
                start = child.start_byte
                end = child.end_byte
                return code[start:end]
        return ""

    def _extract_dependencies(self, node: Node, code: str) -> List[str]:
        """Extract simple dependencies from function (includes, type names)."""
        # This is a simplified version - full dependency extraction
        # would require semantic analysis
        dependencies = []

        # Collect type names used in the function
        type_names = self._find_type_names(node)
        dependencies.extend(type_names)

        return dependencies

    def _find_type_names(self, node: Node) -> List[str]:
        """Find all type names (class, struct references) in a node."""
        types = []
        self._collect_types(node, types)
        return list(set(types))  # Remove duplicates

    def _collect_types(self, node: Node, types: List[str]) -> None:
        """Recursively collect type names."""
        if node.type == "type_identifier":
            type_name = (
                node.text.decode("utf-8") if isinstance(node.text, bytes) else node.text
            )
            types.append(type_name)

        for child in node.children:
            self._collect_types(child, types)
