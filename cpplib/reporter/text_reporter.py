"""Text-based reporting for cpplib analysis results."""

from typing import List, Optional, Dict, Any
from pathlib import Path
from cpplib.pieces.code_piece import CodePiece
from cpplib.semantic.scope_resolver import Symbol


class TextReporter:
    """Generate compact text reports of cpplib analysis."""

    def __init__(self, compact: bool = True, max_width: int = 100):
        """
        Initialize the reporter.

        Args:
            compact: Use compact output (minimal whitespace)
            max_width: Maximum line width for output
        """
        self.compact = compact
        self.max_width = max_width

    def _truncate(self, text: str, width: Optional[int] = None) -> str:
        """Truncate text to fit width."""
        if width is None:
            width = self.max_width
        if len(text) > width:
            return text[: width - 3] + "..."
        return text

    def symbols_summary(self, functions: List[Symbol], classes: List[Symbol]) -> str:
        """
        Generate compact summary of all symbols.

        Args:
            functions: List of function symbols
            classes: List of class symbols

        Returns:
            Formatted text report
        """
        lines = []

        if self.compact:
            # Compact one-liner format
            func_names = ", ".join([f.name for f in functions])
            class_names = ", ".join([c.name for c in classes])

            lines.append(f"Functions ({len(functions)}): {self._truncate(func_names)}")
            if classes:
                lines.append(f"Classes ({len(classes)}): {self._truncate(class_names)}")
        else:
            # Expanded format
            lines.append(f"FUNCTIONS ({len(functions)}):")
            for func in functions:
                file_name = Path(func.file_path).name if func.file_path else "?"
                lines.append(f"  {func.name:30} [{file_name}:{func.line_start}]")

            if classes:
                lines.append(f"\nCLASSES ({len(classes)}):")
                for cls in classes:
                    file_name = Path(cls.file_path).name if cls.file_path else "?"
                    lines.append(f"  {cls.name:30} [{file_name}:{cls.line_start}]")

        return "\n".join(lines)

    def code_piece_summary(self, piece: CodePiece) -> str:
        """
        Generate compact summary of a code piece.

        Args:
            piece: CodePiece to summarize

        Returns:
            Formatted text report
        """
        lines = []

        if self.compact:
            # One-liner summary
            file_name = Path(piece.source_file).name if piece.source_file else "?"
            deps = len(piece.dependencies) if piece.dependencies else 0
            lines.append(
                f"{piece.kind.upper()}: {piece.name} @ {file_name}:"
                f"{piece.line_start}-{piece.line_end} | deps:{deps}"
            )
        else:
            # Expanded format
            lines.append(f"Name:       {piece.name}")
            lines.append(f"Kind:       {piece.kind}")
            lines.append(f"File:       {piece.source_file}")
            lines.append(f"Lines:      {piece.line_start}-{piece.line_end}")
            lines.append(f"Signature:  {self._truncate(piece.signature)}")

            if piece.dependencies:
                lines.append(f"Dependencies ({len(piece.dependencies)}):")
                for dep in piece.dependencies:
                    lines.append(f"  - {dep}")

        return "\n".join(lines)

    def dependency_report(self, symbol_name: str, dependencies: Dict[str, Any]) -> str:
        """
        Generate compact dependency report.

        Args:
            symbol_name: Name of the symbol
            dependencies: Dependency dict (from get_dependencies)

        Returns:
            Formatted text report
        """
        lines = []
        lines.append(f"Dependencies for '{symbol_name}':")

        if not dependencies or not any(dependencies.values()):
            lines.append("  (none)")
            return "\n".join(lines)

        for dep_type, dep_list in dependencies.items():
            if dep_list:
                if self.compact:
                    dep_str = ", ".join(dep_list)
                    lines.append(f"  {dep_type}: {self._truncate(dep_str)}")
                else:
                    lines.append(f"  {dep_type}:")
                    for dep in dep_list:
                        lines.append(f"    - {dep}")

        return "\n".join(lines)

    def codebase_statistics(
        self,
        function_count: int,
        class_count: int,
        file_count: int,
        total_lines: int = 0,
    ) -> str:
        """
        Generate codebase statistics.

        Args:
            function_count: Total functions
            class_count: Total classes
            file_count: Total files
            total_lines: Total lines of code (optional)

        Returns:
            Formatted text report
        """
        lines = []

        if self.compact:
            lines.append(
                f"Stats: {file_count} files | {function_count} functions | "
                f"{class_count} classes"
            )
            if total_lines:
                lines.append(f"       {total_lines} lines of code")
        else:
            lines.append("CODEBASE STATISTICS")
            lines.append(f"  Files:     {file_count}")
            lines.append(f"  Functions: {function_count}")
            lines.append(f"  Classes:   {class_count}")
            if total_lines:
                lines.append(f"  Lines:     {total_lines}")

        return "\n".join(lines)

    def validation_result(self, success: bool, message: str, error_details: str = "") -> str:
        """
        Generate validation result report.

        Args:
            success: Build success status
            message: Summary message
            error_details: Detailed error output (optional)

        Returns:
            Formatted text report
        """
        lines = []

        status = "✓ PASS" if success else "✗ FAIL"
        lines.append(f"{status}: {message}")

        if not success and error_details:
            if self.compact:
                # Show first line of error
                first_error = error_details.split("\n")[0]
                lines.append(f"  Error: {self._truncate(first_error, self.max_width - 10)}")
            else:
                lines.append("\nError details:")
                for line in error_details.split("\n")[:10]:
                    if line.strip():
                        lines.append(f"  {line}")
                if len(error_details.split("\n")) > 10:
                    lines.append("  ...")

        return "\n".join(lines)

    def extraction_summary(self, pieces: List[CodePiece]) -> str:
        """
        Generate summary of extracted code pieces.

        Args:
            pieces: List of CodePiece objects

        Returns:
            Formatted text report
        """
        if not pieces:
            return "No pieces extracted"

        lines = []

        if self.compact:
            lines.append(f"Extracted {len(pieces)} pieces:")
            for piece in pieces:
                file_name = Path(piece.source_file).name if piece.source_file else "?"
                lines.append(f"  • {piece.name} ({piece.kind}) @ {file_name}:{piece.line_start}")
        else:
            lines.append(f"EXTRACTED PIECES ({len(pieces)}):")
            for i, piece in enumerate(pieces, 1):
                file_name = Path(piece.source_file).name if piece.source_file else "?"
                lines.append(f"{i}. {piece.name}")
                lines.append(f"   Kind:  {piece.kind}")
                lines.append(f"   File:  {file_name}:{piece.line_start}-{piece.line_end}")
                if piece.dependencies:
                    lines.append(f"   Deps:  {len(piece.dependencies)}")

        return "\n".join(lines)

    def comparison_table(self, items: List[Dict[str, str]], columns: List[str]) -> str:
        """
        Generate aligned table for item comparison.

        Args:
            items: List of dicts with item data
            columns: Column names to display

        Returns:
            Formatted text table
        """
        if not items:
            return "(empty)"

        # Calculate column widths
        col_widths = {}
        for col in columns:
            col_widths[col] = len(col)
            for item in items:
                col_widths[col] = max(col_widths[col], len(str(item.get(col, ""))))

        lines = []

        # Header
        header_parts = []
        for col in columns:
            header_parts.append(col.ljust(col_widths[col]))
        lines.append(" | ".join(header_parts))

        # Separator
        sep_parts = ["-" * col_widths[col] for col in columns]
        lines.append("-+-".join(sep_parts))

        # Rows
        for item in items:
            row_parts = []
            for col in columns:
                value = str(item.get(col, ""))
                row_parts.append(value.ljust(col_widths[col]))
            lines.append(" | ".join(row_parts))

        return "\n".join(lines)


class AnalysisReporter:
    """High-level reporter for complete codebase analysis."""

    def __init__(self, codebase: "CPPCodebase", compact: bool = True):
        """
        Initialize analysis reporter.

        Args:
            codebase: CPPCodebase instance
            compact: Use compact output
        """
        self.codebase = codebase
        self.text_reporter = TextReporter(compact=compact)

    def full_analysis(self) -> str:
        """
        Generate complete analysis report.

        Returns:
            Formatted text report
        """
        lines = []

        # Get data
        functions = self.codebase.get_all_functions()
        classes = self.codebase.get_all_classes()
        files = self.codebase.file_index.list_files()

        # Statistics
        lines.append(self.text_reporter.codebase_statistics(
            len(functions), len(classes), len(files)
        ))
        lines.append("")

        # Symbols
        lines.append(self.text_reporter.symbols_summary(functions, classes))

        return "\n".join(lines)

    def symbol_details(self, symbol_name: str) -> str:
        """
        Generate detailed report for a symbol.

        Args:
            symbol_name: Symbol name to report on

        Returns:
            Formatted text report
        """
        lines = []

        # Try to extract the symbol
        piece = self.codebase.extract_function(symbol_name)
        if not piece:
            piece = self.codebase.extract_class(symbol_name)

        if not piece:
            return f"Symbol '{symbol_name}' not found"

        lines.append(self.text_reporter.code_piece_summary(piece))
        lines.append("")

        # Dependencies
        deps = self.codebase.get_dependencies(symbol_name)
        lines.append(self.text_reporter.dependency_report(symbol_name, deps))

        return "\n".join(lines)
