"""Build and track dependencies between code elements."""

from typing import Dict, Set, List, Optional, DefaultDict
from collections import defaultdict
from pathlib import Path
from tree_sitter import Node


class Dependency:
    """Represents a dependency between code elements."""

    def __init__(self, source: str, target: str, dep_type: str, file_path: str, line_no: int):
        """
        Initialize a Dependency.

        Args:
            source: Name of code element that depends
            target: Name of code element being depended on
            dep_type: Type of dependency (include, type, call, etc.)
            file_path: File where dependency occurs
            line_no: Line number
        """
        self.source = source
        self.target = target
        self.dep_type = dep_type
        self.file_path = file_path
        self.line_no = line_no

    def __repr__(self) -> str:
        return f"Dependency({self.source} -> {self.target} [{self.dep_type}])"


class DependencyGraph:
    """Build a dependency graph for a C++ codebase."""

    def __init__(self):
        """Initialize the dependency graph."""
        self.dependencies: List[Dependency] = []
        self.graph: DefaultDict[str, Set[str]] = defaultdict(set)  # source -> targets
        self.reverse_graph: DefaultDict[str, Set[str]] = defaultdict(set)  # target -> sources
        self.includes: Dict[str, Set[str]] = defaultdict(set)  # file -> included files
        self.type_dependencies: DefaultDict[str, Set[str]] = defaultdict(set)  # func -> types

    def add_dependency(
        self,
        source: str,
        target: str,
        dep_type: str,
        file_path: str,
        line_no: int = 0,
    ) -> None:
        """
        Add a dependency to the graph.

        Args:
            source: Name of code element that depends
            target: Name of code element being depended on
            dep_type: Type of dependency (include, type, call, use, etc.)
            file_path: File where dependency occurs
            line_no: Line number
        """
        dep = Dependency(source, target, dep_type, file_path, line_no)
        self.dependencies.append(dep)
        self.graph[source].add(target)
        self.reverse_graph[target].add(source)

    def add_include(self, file_path: str, included_file: str) -> None:
        """
        Add an include relationship.

        Args:
            file_path: File that includes
            included_file: File being included
        """
        self.includes[file_path].add(included_file)

    def add_type_dependency(self, function: str, type_name: str) -> None:
        """
        Add a type dependency for a function.

        Args:
            function: Function name
            type_name: Type name being used
        """
        self.type_dependencies[function].add(type_name)

    def get_dependencies_of(self, symbol: str) -> Set[str]:
        """Get all symbols that a symbol depends on."""
        return self.graph.get(symbol, set())

    def get_dependents_of(self, symbol: str) -> Set[str]:
        """Get all symbols that depend on a symbol."""
        return self.reverse_graph.get(symbol, set())

    def get_transitive_dependencies(self, symbol: str, max_depth: int = -1) -> Set[str]:
        """
        Get all transitive dependencies of a symbol.

        Args:
            symbol: Symbol to analyze
            max_depth: Maximum recursion depth (-1 for unlimited)

        Returns:
            Set of all transitive dependencies
        """
        result: Set[str] = set()
        visited: Set[str] = set()
        self._collect_transitive(symbol, result, visited, max_depth)
        result.discard(symbol)  # Remove self
        return result

    def _collect_transitive(
        self, symbol: str, result: Set[str], visited: Set[str], max_depth: int
    ) -> None:
        """Recursively collect transitive dependencies."""
        if max_depth == 0 or symbol in visited:
            return

        visited.add(symbol)
        deps = self.get_dependencies_of(symbol)

        for dep in deps:
            result.add(dep)
            if max_depth != 1:
                self._collect_transitive(
                    dep, result, visited, max_depth - 1 if max_depth > 0 else -1
                )

    def extract_dependencies_from_code(
        self, node: Node, file_path: str, code: str, symbol_name: str
    ) -> None:
        """
        Extract dependencies from a code node.

        Args:
            node: AST node to analyze
            file_path: File path
            code: Source code
            symbol_name: Name of the symbol being analyzed
        """
        self._extract_deps_recursive(node, file_path, code, symbol_name)

    def _extract_deps_recursive(
        self, node: Node, file_path: str, code: str, symbol_name: str
    ) -> None:
        """Recursively extract dependencies from AST."""
        # Track type uses
        if node.type == "type_identifier":
            type_name = (
                node.text.decode("utf-8") if isinstance(node.text, bytes) else node.text
            )
            self.add_type_dependency(symbol_name, type_name)
            self.add_dependency(
                symbol_name,
                type_name,
                "type",
                file_path,
                node.start_point[0] + 1,
            )

        # Track function calls
        elif node.type == "call_expression":
            # Get function name being called
            for child in node.children:
                if child.type == "identifier":
                    func_name = (
                        child.text.decode("utf-8") if isinstance(child.text, bytes) else child.text
                    )
                    self.add_dependency(
                        symbol_name,
                        func_name,
                        "call",
                        file_path,
                        node.start_point[0] + 1,
                    )
                    break

        # Recurse
        for child in node.children:
            self._extract_deps_recursive(child, file_path, code, symbol_name)

    def get_include_chain(self, start_file: str, target_file: str) -> Optional[List[str]]:
        """
        Find an include chain from start_file to target_file (BFS).

        Args:
            start_file: Starting file
            target_file: Target file

        Returns:
            List of files in the include chain, or None if not reachable
        """
        from collections import deque

        queue = deque([(start_file, [start_file])])
        visited = {start_file}

        while queue:
            current, path = queue.popleft()

            if current == target_file:
                return path

            for included in self.includes.get(current, set()):
                if included not in visited:
                    visited.add(included)
                    queue.append((included, path + [included]))

        return None

    def get_all_dependencies_for_function(self, func_name: str) -> Dict[str, Set[str]]:
        """
        Get all dependencies for a function (includes types, calls, etc.).

        Args:
            func_name: Function name

        Returns:
            Dict with keys 'types', 'calls', 'uses' etc.
        """
        result = {"types": self.type_dependencies.get(func_name, set()), "calls": set()}

        # Get calls
        for dep in self.dependencies:
            if dep.source == func_name and dep.dep_type == "call":
                result["calls"].add(dep.target)

        return result

    def print_graph(self) -> None:
        """Print the dependency graph for debugging."""
        print("Dependency Graph:")
        for source, targets in sorted(self.graph.items()):
            for target in sorted(targets):
                dep_type = next(
                    (d.dep_type for d in self.dependencies if d.source == source and d.target == target),
                    "unknown",
                )
                print(f"  {source} -> {target} [{dep_type}]")
