"""Generate and format C++ code from CodePiece objects."""

from typing import Optional, List
from cpplib.pieces.code_piece import CodePiece


class CodeGenerator:
    """Generate C++ code from CodePiece abstractions."""

    def __init__(self):
        """Initialize the code generator."""
        self.indent = "    "  # 4 spaces

    def generate_function(self, piece: CodePiece) -> str:
        """
        Generate complete function code from a CodePiece.

        Args:
            piece: CodePiece representing a function

        Returns:
            Formatted function code ready to insert
        """
        if piece.kind != "function":
            raise ValueError(f"Expected function kind, got {piece.kind}")

        # Build includes
        includes = self._generate_includes(piece)

        # Build function
        func_code = f"{piece.signature} {piece.body}\n"

        return includes + func_code

    def generate_class(self, piece: CodePiece) -> str:
        """
        Generate complete class code from a CodePiece.

        Args:
            piece: CodePiece representing a class

        Returns:
            Formatted class code ready to insert
        """
        if piece.kind not in ["class", "struct"]:
            raise ValueError(f"Expected class/struct kind, got {piece.kind}")

        # Build includes
        includes = self._generate_includes(piece)

        # Build forward declarations if needed
        forward_decls = self._generate_forward_declarations(piece)

        # Build class - combine signature and body if not already combined
        if piece.body.strip().startswith("{"):
            class_code = piece.signature + " " + piece.body + "\n"
        else:
            class_code = piece.body + "\n"

        return includes + forward_decls + class_code

    def _generate_includes(self, piece: CodePiece) -> str:
        """Generate include directives for a piece."""
        includes = set()

        # Extract STL includes from dependencies
        std_mapping = {
            "vector": "#include <vector>\n",
            "map": "#include <map>\n",
            "set": "#include <set>\n",
            "string": "#include <string>\n",
            "iostream": "#include <iostream>\n",
            "memory": "#include <memory>\n",
            "array": "#include <array>\n",
            "list": "#include <list>\n",
            "queue": "#include <queue>\n",
            "stack": "#include <stack>\n",
            "deque": "#include <deque>\n",
            "algorithm": "#include <algorithm>\n",
            "numeric": "#include <numeric>\n",
        }

        for dep in piece.dependencies:
            for std_type, include in std_mapping.items():
                if std_type in dep.lower():
                    includes.add(include)

        if includes:
            return "".join(sorted(includes)) + "\n"
        return ""

    def _generate_forward_declarations(self, piece: CodePiece) -> str:
        """Generate forward declarations for a piece."""
        forward_decls = []

        # Extract class/struct names from dependencies
        for dep in piece.dependencies:
            # Simple heuristic: if it looks like a class name (capitalized)
            if dep and dep[0].isupper() and dep.replace("_", "").isalnum():
                # Check if it's used as pointer/reference
                if "*" in piece.body or "&" in piece.body:
                    forward_decls.append(f"class {dep};")

        if forward_decls:
            return "\n".join(forward_decls) + "\n\n"
        return ""

    def format_code(self, code: str, indent_level: int = 0) -> str:
        """
        Format code with proper indentation.

        Args:
            code: Code to format
            indent_level: Indentation level

        Returns:
            Formatted code
        """
        indent_str = self.indent * indent_level
        lines = code.split("\n")
        formatted = []

        for line in lines:
            if line.strip():
                formatted.append(indent_str + line)
            else:
                formatted.append("")

        return "\n".join(formatted)

    def merge_code(self, original: str, new_code: str, position: str = "end") -> str:
        """
        Merge new code into original code.

        Args:
            original: Original source code
            new_code: Code to insert
            position: Where to insert ('start', 'end', or before/after a marker)

        Returns:
            Merged code
        """
        if position == "start":
            return new_code + "\n" + original
        elif position == "end":
            return original + "\n" + new_code
        else:
            # Assume position is a line marker to insert after
            lines = original.split("\n")
            for i, line in enumerate(lines):
                if position in line:
                    lines.insert(i + 1, new_code)
                    return "\n".join(lines)
            # If marker not found, append to end
            return original + "\n" + new_code

    def generate_header_guard(self, filename: str) -> tuple[str, str]:
        """
        Generate header guard for a file.

        Args:
            filename: Header filename (e.g., "MyClass.h")

        Returns:
            Tuple of (opening guard, closing guard)
        """
        guard_name = filename.upper().replace(".", "_").replace("-", "_")
        guard_name = f"{guard_name}_H"

        opening = f"#ifndef {guard_name}\n#define {guard_name}\n\n"
        closing = f"\n#endif  // {guard_name}\n"

        return opening, closing

    def wrap_in_namespace(self, code: str, namespace: str) -> str:
        """
        Wrap code in a namespace.

        Args:
            code: Code to wrap
            namespace: Namespace name

        Returns:
            Code wrapped in namespace
        """
        opening = f"namespace {namespace} {{\n"
        closing = f"}}  // namespace {namespace}\n"
        indented = self.format_code(code, indent_level=1)
        return opening + indented + closing
