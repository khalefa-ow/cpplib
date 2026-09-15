"""Semantic analysis for C++ codebases."""

from pathlib import Path
from typing import Dict, List, Optional
from tree_sitter import Node

from cpplib.parser.file_index import FileIndex
from cpplib.parser.cpp_parser import CPPParser
from cpplib.semantic.scope_resolver import ScopeResolver, Symbol
from cpplib.semantic.dependency_graph import DependencyGraph


class SemanticAnalyzer:
    """Perform semantic analysis on C++ codebases."""

    def __init__(self, file_index: FileIndex):
        """
        Initialize the semantic analyzer.

        Args:
            file_index: FileIndex instance for the codebase
        """
        self.file_index = file_index
        self.parser = CPPParser()
        self.scope_resolver = ScopeResolver()
        self.dependency_graph = DependencyGraph()
        self.all_symbols: Dict[str, Symbol] = {}
        self.file_symbols: Dict[Path, Dict[str, Symbol]] = {}
        self._analyzed = False

    def analyze(self) -> None:
        """Perform full semantic analysis of the codebase."""
        # Analyze scopes in all files
        for file_path in self.file_index.list_files():
            self._analyze_file_scopes(file_path)

        # Extract dependencies
        for file_path in self.file_index.list_files():
            self._extract_file_dependencies(file_path)

        self._analyzed = True

    def _analyze_file_scopes(self, file_path: Path) -> None:
        """Analyze scopes for a single file."""
        ast = self.file_index.get_ast(file_path)
        code = self.file_index.get_content(file_path)

        if not ast or not code:
            return

        # Resolve scopes
        scope_resolver = ScopeResolver()
        symbols = scope_resolver.resolve_scopes(ast, str(file_path), code)

        # Store symbols
        self.file_symbols[file_path] = symbols
        self.all_symbols.update(symbols)

    def _extract_file_dependencies(self, file_path: Path) -> None:
        """Extract dependencies for a single file."""
        ast = self.file_index.get_ast(file_path)
        code = self.file_index.get_content(file_path)

        if not ast or not code:
            return

        # Extract includes
        includes = self.parser.find_includes(ast)
        for include in includes:
            # Simple include extraction (would need more processing for real paths)
            self.dependency_graph.add_include(str(file_path), include)

        # Extract function dependencies
        functions = self.parser.find_functions(ast)
        for func_node in functions:
            func_name = self._get_func_name(func_node)
            if func_name:
                self.dependency_graph.extract_dependencies_from_code(
                    func_node, str(file_path), code, func_name
                )

    def _get_func_name(self, func_node: Node) -> Optional[str]:
        """Extract function name from a function_definition node."""
        for child in func_node.children:
            if child.type in ["declarator", "function_declarator", "pointer_declarator"]:
                name = self._find_identifier(child)
                if name:
                    return name
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

    def get_symbol(self, name: str) -> Optional[Symbol]:
        """Look up a symbol by name."""
        if not self._analyzed:
            self.analyze()

        # Try exact match first
        if name in self.all_symbols:
            return self.all_symbols[name]

        # Try short name match
        for sym in self.all_symbols.values():
            if sym.name == name:
                return sym

        return None

    def get_symbols_in_file(self, file_path: Path) -> List[Symbol]:
        """Get all symbols defined in a file."""
        if not self._analyzed:
            self.analyze()

        return list(self.file_symbols.get(file_path, {}).values())

    def get_symbols_by_kind(self, kind: str) -> List[Symbol]:
        """Get all symbols of a specific kind."""
        if not self._analyzed:
            self.analyze()

        return [sym for sym in self.all_symbols.values() if sym.kind == kind]

    def get_dependencies(self, symbol_name: str) -> Dict[str, any]:
        """Get all dependencies for a symbol."""
        if not self._analyzed:
            self.analyze()

        return self.dependency_graph.get_all_dependencies_for_function(symbol_name)

    def get_transitive_dependencies(self, symbol_name: str) -> set:
        """Get transitive dependencies for a symbol."""
        if not self._analyzed:
            self.analyze()

        return self.dependency_graph.get_transitive_dependencies(symbol_name)

    def list_all_functions(self) -> List[Symbol]:
        """Get all functions in the codebase."""
        return self.get_symbols_by_kind("function")

    def list_all_classes(self) -> List[Symbol]:
        """Get all classes in the codebase."""
        return [sym for sym in self.all_symbols.values() if sym.kind in ["class", "struct"]]
