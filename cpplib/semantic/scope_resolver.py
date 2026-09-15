"""Resolve scopes and track symbol visibility in C++ code."""

from typing import Dict, Set, List, Optional, Tuple
from tree_sitter import Node


class Symbol:
    """Represents a C++ symbol (function, variable, type, etc.)."""

    def __init__(
        self,
        name: str,
        kind: str,
        scope: str,
        file_path: str,
        line_start: int,
        line_end: int,
        full_name: Optional[str] = None,
    ):
        """
        Initialize a Symbol.

        Args:
            name: Symbol name
            kind: Symbol kind (function, variable, class, struct, typedef, etc.)
            scope: Scope level (global, class, function, block)
            file_path: File where symbol is defined
            line_start: Starting line number
            line_end: Ending line number
            full_name: Fully qualified name (e.g., "MyClass::myMethod")
        """
        self.name = name
        self.kind = kind
        self.scope = scope
        self.file_path = file_path
        self.line_start = line_start
        self.line_end = line_end
        self.full_name = full_name or name
        self.forward_declared = False

    def __repr__(self) -> str:
        return f"Symbol({self.full_name}, {self.kind}, {self.scope})"


class ScopeResolver:
    """Resolve scopes and build a symbol table for C++ code."""

    def __init__(self):
        """Initialize the scope resolver."""
        self.symbols: Dict[str, Symbol] = {}  # full_name -> Symbol
        self.scope_stack: List[str] = ["global"]
        self.class_stack: List[str] = []  # Track nested class names

    def resolve_scopes(self, node: Node, file_path: str, code: str) -> Dict[str, Symbol]:
        """
        Resolve all scopes in an AST and return symbol table.

        Args:
            node: Root node of AST
            file_path: Path to the source file
            code: Source code

        Returns:
            Dictionary of fully qualified name -> Symbol
        """
        self.symbols.clear()
        self.scope_stack = ["global"]
        self.class_stack = []

        self._resolve_node(node, file_path, code)
        return self.symbols

    def _resolve_node(self, node: Node, file_path: str, code: str) -> None:
        """Recursively resolve scopes in AST."""
        # Handle class/struct definitions
        if node.type in ["class_specifier", "struct_specifier"]:
            class_name = self._extract_name(node)
            if class_name:
                self.class_stack.append(class_name)
                self._register_symbol(
                    class_name,
                    node.type.split("_")[0],  # 'class' or 'struct'
                    self._current_scope(),
                    file_path,
                    node.start_point[0] + 1,
                    node.end_point[0] + 1,
                )

        # Handle function definitions
        elif node.type == "function_definition":
            func_name = self._extract_function_name(node)
            if func_name:
                current_class = self.class_stack[-1] if self.class_stack else None
                full_name = f"{current_class}::{func_name}" if current_class else func_name
                self._register_symbol(
                    func_name,
                    "function",
                    self._current_scope(),
                    file_path,
                    node.start_point[0] + 1,
                    node.end_point[0] + 1,
                    full_name,
                )

        # Handle declarations (variables, typedefs, etc.)
        elif node.type == "declaration":
            self._handle_declaration(node, file_path)

        # Handle forward declarations
        elif node.type == "class_declaration":
            class_name = self._extract_name(node)
            if class_name:
                self._register_symbol(
                    class_name,
                    "class",
                    self._current_scope(),
                    file_path,
                    node.start_point[0] + 1,
                    node.end_point[0] + 1,
                )
                self.symbols[class_name].forward_declared = True

        # Recurse into children
        for child in node.children:
            self._resolve_node(child, file_path, code)

        # Pop from class stack when leaving a class scope
        if node.type in ["class_specifier", "struct_specifier"]:
            if self.class_stack:
                self.class_stack.pop()

    def _handle_declaration(self, node: Node, file_path: str) -> None:
        """Handle variable, typedef, and function declarations."""
        # Extract declarator names
        for child in node.children:
            if child.type in ["declarator", "pointer_declarator", "reference_declarator"]:
                name = self._find_identifier(child)
                if name:
                    kind = self._determine_declaration_kind(node)
                    self._register_symbol(
                        name,
                        kind,
                        self._current_scope(),
                        file_path,
                        node.start_point[0] + 1,
                        node.end_point[0] + 1,
                    )

    def _register_symbol(
        self,
        name: str,
        kind: str,
        scope: str,
        file_path: str,
        line_start: int,
        line_end: int,
        full_name: Optional[str] = None,
    ) -> None:
        """Register a symbol in the symbol table."""
        full_name = full_name or name
        symbol = Symbol(name, kind, scope, file_path, line_start, line_end, full_name)
        self.symbols[full_name] = symbol

    def _extract_name(self, node: Node) -> Optional[str]:
        """Extract class/struct name from specifier node."""
        for child in node.children:
            if child.type == "type_identifier":
                return child.text.decode("utf-8") if isinstance(child.text, bytes) else child.text
            elif child.type == "class_name":
                return child.text.decode("utf-8") if isinstance(child.text, bytes) else child.text
        return None

    def _extract_function_name(self, node: Node) -> Optional[str]:
        """Extract function name from function_definition node."""
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

    def _determine_declaration_kind(self, node: Node) -> str:
        """Determine what kind of declaration this is."""
        text = (
            node.text.decode("utf-8") if isinstance(node.text, bytes) else str(node.text)
        )

        if "typedef" in text:
            return "typedef"
        elif "using" in text:
            return "using"
        else:
            return "variable"

    def _current_scope(self) -> str:
        """Get the current scope."""
        if self.class_stack:
            return "class"
        return "global"

    def lookup_symbol(self, name: str) -> Optional[Symbol]:
        """Look up a symbol by name (exact or full name)."""
        # Try exact match first
        if name in self.symbols:
            return self.symbols[name]

        # Try finding by short name
        for sym in self.symbols.values():
            if sym.name == name:
                return sym

        return None

    def get_symbols_in_file(self, file_path: str) -> List[Symbol]:
        """Get all symbols defined in a file."""
        return [sym for sym in self.symbols.values() if sym.file_path == file_path]

    def get_symbols_by_kind(self, kind: str) -> List[Symbol]:
        """Get all symbols of a specific kind."""
        return [sym for sym in self.symbols.values() if sym.kind == kind]
