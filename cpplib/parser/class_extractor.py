"""Extract classes and structs with their members and dependencies."""

from pathlib import Path
from typing import Optional, List, Dict
from tree_sitter import Node
from cpplib.pieces.code_piece import CodePiece
from cpplib.parser.cpp_parser import CPPParser


class ClassExtractor:
    """Extract class/struct definitions and metadata from C++ code."""

    def __init__(self, parser: CPPParser):
        """
        Initialize the class extractor.

        Args:
            parser: CPPParser instance
        """
        self.parser = parser

    def extract_classes(self, file_path: Path, code: str) -> List[CodePiece]:
        """
        Extract all classes/structs from a C++ file.

        Args:
            file_path: Path to the C++ file
            code: Source code as string

        Returns:
            List of CodePiece objects representing classes
        """
        ast = self.parser.parse_string(code)
        if not ast:
            return []

        classes = []
        self._collect_classes(ast, file_path, code, classes)
        return classes

    def extract_class_by_name(
        self, file_path: Path, code: str, class_name: str
    ) -> Optional[CodePiece]:
        """
        Extract a specific class by name.

        Args:
            file_path: Path to the C++ file
            code: Source code as string
            class_name: Name of the class to extract

        Returns:
            CodePiece representing the class, or None if not found
        """
        classes = self.extract_classes(file_path, code)
        for cls in classes:
            if cls.name == class_name:
                return cls
        return None

    def _collect_classes(
        self, node: Node, file_path: Path, code: str, result: List[CodePiece]
    ) -> None:
        """Recursively collect class/struct definitions."""
        if node.type in ["class_specifier", "struct_specifier"]:
            piece = self._parse_class_definition(node, file_path, code, node.type)
            if piece:
                result.append(piece)

        for child in node.children:
            self._collect_classes(child, file_path, code, result)

    def _parse_class_definition(
        self, node: Node, file_path: Path, code: str, class_type: str
    ) -> Optional[CodePiece]:
        """Parse a class_specifier or struct_specifier node into a CodePiece."""
        try:
            # Extract class name
            class_name = self._extract_class_name(node)
            if not class_name:
                return None

            # Get full class definition
            class_code = self._extract_class_code(node, code)

            # Get line numbers
            start_line, end_line = self.parser.get_node_line_range(node)

            # Extract base classes
            base_classes = self._extract_base_classes(node)

            # Extract member functions
            member_functions = self._extract_member_names(node, "function_definition")

            # Extract member variables
            member_variables = self._extract_member_names(node, "declaration")

            # Build dependencies
            dependencies = base_classes + self._extract_type_references(node)

            return CodePiece(
                name=class_name,
                kind=class_type.split("_")[0],  # 'class' or 'struct'
                signature=self._extract_class_signature(node, code),
                body=class_code,
                source_file=str(file_path),
                line_start=start_line,
                line_end=end_line,
                dependencies=dependencies,
            )
        except Exception as e:
            print(f"Failed to parse class definition: {e}")
            return None

    def _extract_class_name(self, node: Node) -> Optional[str]:
        """Extract class/struct name from specifier node."""
        for child in node.children:
            if child.type == "type_identifier":
                return child.text.decode("utf-8") if isinstance(child.text, bytes) else child.text
        return None

    def _extract_class_code(self, node: Node, code: str) -> str:
        """Extract the full class definition code."""
        start = node.start_byte
        end = node.end_byte
        return code[start:end]

    def _extract_class_signature(self, node: Node, code: str) -> str:
        """Extract class signature (class keyword through opening brace)."""
        start = node.start_byte

        # Find opening brace
        for child in node.children:
            if child.type == "field_declaration_list":
                end = child.start_byte
                return code[start:end].strip()

        return code[start : node.end_byte].strip()

    def _extract_base_classes(self, node: Node) -> List[str]:
        """Extract base class names."""
        bases = []

        for child in node.children:
            if child.type == "base_class_clause":
                self._collect_base_classes(child, bases)

        return bases

    def _collect_base_classes(self, node: Node, result: List[str]) -> None:
        """Recursively collect base class names."""
        if node.type == "type_identifier":
            name = (
                node.text.decode("utf-8") if isinstance(node.text, bytes) else node.text
            )
            result.append(name)

        for child in node.children:
            self._collect_base_classes(child, result)

    def _extract_member_names(self, node: Node, member_type: str) -> List[str]:
        """Extract names of class members (functions or variables)."""
        members = []

        for child in node.children:
            if child.type == "field_declaration_list":
                self._collect_members(child, member_type, members)

        return members

    def _collect_members(self, node: Node, member_type: str, result: List[str]) -> None:
        """Recursively collect member names."""
        if node.type == member_type:
            name = self._extract_name_from_member(node)
            if name:
                result.append(name)

        for child in node.children:
            self._collect_members(child, member_type, result)

    def _extract_name_from_member(self, node: Node) -> Optional[str]:
        """Extract name from a member node."""
        for child in node.children:
            if child.type in ["declarator", "function_declarator", "pointer_declarator"]:
                return self._find_identifier(child)
        return None

    def _find_identifier(self, node: Node) -> Optional[str]:
        """Find the first identifier in a subtree."""
        if node.type == "identifier":
            text = node.text
            return text.decode("utf-8") if isinstance(text, bytes) else text

        for child in node.children:
            result = self._find_identifier(child)
            if result:
                return result
        return None

    def _extract_type_references(self, node: Node) -> List[str]:
        """Extract type references used in the class."""
        types = []
        self._collect_type_refs(node, types)
        return list(set(types))  # Remove duplicates

    def _collect_type_refs(self, node: Node, result: List[str]) -> None:
        """Recursively collect type references."""
        if node.type == "type_identifier":
            type_name = (
                node.text.decode("utf-8") if isinstance(node.text, bytes) else node.text
            )
            if type_name:
                result.append(type_name)

        for child in node.children:
            self._collect_type_refs(child, result)
