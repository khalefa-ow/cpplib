"""Collect all dependencies for a code piece (function or class)."""

from typing import Set, List, Dict, Optional
from cpplib.semantic.analyzer import SemanticAnalyzer
from cpplib.pieces.code_piece import CodePiece


class DependencyCollector:
    """Collect all dependencies needed to extract a piece of code."""

    def __init__(self, analyzer: SemanticAnalyzer):
        """
        Initialize the dependency collector.

        Args:
            analyzer: SemanticAnalyzer instance
        """
        self.analyzer = analyzer

    def collect_function_dependencies(self, func_name: str) -> Dict[str, any]:
        """
        Collect all dependencies for a function.

        Args:
            func_name: Name of the function

        Returns:
            Dict with keys: 'includes', 'types', 'functions', 'classes', 'forward_decls'
        """
        result = {
            "includes": set(),
            "types": set(),
            "functions": set(),
            "classes": set(),
            "forward_decls": set(),
        }

        # Get direct dependencies from the semantic analyzer
        deps = self.analyzer.get_dependencies(func_name)

        # Collect types used
        result["types"].update(deps.get("types", set()))

        # Collect function calls
        result["functions"].update(deps.get("calls", set()))

        # Find all classes referenced by types
        for type_name in result["types"]:
            class_sym = self.analyzer.get_symbol(type_name)
            if class_sym and class_sym.kind in ["class", "struct"]:
                result["classes"].add(type_name)

        # Add transitive dependencies
        transitive = self.analyzer.get_transitive_dependencies(func_name)
        result["functions"].update(transitive)

        return result

    def collect_class_dependencies(self, class_name: str) -> Dict[str, any]:
        """
        Collect all dependencies for a class.

        Args:
            class_name: Name of the class

        Returns:
            Dict with keys: 'includes', 'base_classes', 'types', 'member_functions'
        """
        result = {
            "includes": set(),
            "base_classes": set(),
            "types": set(),
            "member_functions": set(),
        }

        # Get the class symbol
        class_sym = self.analyzer.get_symbol(class_name)
        if not class_sym:
            return result

        # Collect dependencies from all member functions
        for func_sym in self.analyzer.list_all_functions():
            if func_sym.full_name.startswith(f"{class_name}::"):
                func_deps = self.collect_function_dependencies(func_sym.name)
                result["types"].update(func_deps["types"])
                result["member_functions"].add(func_sym.name)

        return result

    def extract_with_dependencies(self, piece: CodePiece) -> CodePiece:
        """
        Enhance a CodePiece with collected dependencies.

        Args:
            piece: Original CodePiece

        Returns:
            Enhanced CodePiece with full dependency information
        """
        if piece.kind == "function":
            deps = self.collect_function_dependencies(piece.name)
        elif piece.kind in ["class", "struct"]:
            deps = self.collect_class_dependencies(piece.name)
        else:
            deps = {}

        # Flatten dependencies into a list of strings
        all_deps = []
        for dep_type, dep_set in deps.items():
            if isinstance(dep_set, set):
                all_deps.extend(list(dep_set))

        piece.dependencies = all_deps
        return piece

    def get_minimal_includes(self, piece: CodePiece) -> List[str]:
        """
        Determine minimal includes needed for a piece.

        Args:
            piece: CodePiece to analyze

        Returns:
            List of include directives needed
        """
        includes = set()

        # Common includes based on STL types used
        dependencies = piece.get_dependencies()
        std_mapping = {
            "vector": "<vector>",
            "map": "<map>",
            "set": "<set>",
            "string": "<string>",
            "iostream": "<iostream>",
            "memory": "<memory>",
            "array": "<array>",
            "list": "<list>",
            "queue": "<queue>",
            "stack": "<stack>",
            "deque": "<deque>",
            "functional": "<functional>",
            "algorithm": "<algorithm>",
            "numeric": "<numeric>",
        }

        for dep in dependencies:
            for std_type, include in std_mapping.items():
                if std_type in dep.lower():
                    includes.add(include)

        return list(includes)

    def find_forward_declarations_needed(self, piece: CodePiece) -> List[str]:
        """
        Determine which forward declarations are needed.

        Args:
            piece: CodePiece to analyze

        Returns:
            List of forward declarations needed
        """
        forward_decls = []
        dependencies = piece.get_dependencies()

        for dep in dependencies:
            # If it's a class/struct that's only used as pointer/reference
            class_sym = self.analyzer.get_symbol(dep)
            if class_sym and class_sym.kind in ["class", "struct"]:
                # Check if it's used as a pointer or reference in the code
                if "*" in piece.body or "&" in piece.body:
                    if f"{class_sym.kind} {dep};" not in forward_decls:
                        forward_decls.append(f"class {dep};")

        return forward_decls
