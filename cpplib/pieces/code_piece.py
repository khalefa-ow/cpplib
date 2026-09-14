"""Code piece abstraction - functions/classes with their dependencies."""

from dataclasses import dataclass, field
from typing import List, Optional, Set


@dataclass
class CodePiece:
    """
    Represents an extractable code unit (function, class, etc.) with dependencies.

    Attributes:
        name: Function/class name
        kind: 'function', 'method', or 'class'
        signature: Function/method signature or class definition
        body: Implementation code
        dependencies: List of required includes, types, and other functions
        source_file: Original file path
        line_start: Starting line number
        line_end: Ending line number
    """

    name: str
    kind: str  # 'function', 'method', 'class'
    signature: str
    body: str
    source_file: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    dependencies: List[str] = field(default_factory=list)

    def get_dependencies(self) -> Set[str]:
        """Return set of all dependencies (includes, types, functions)."""
        return set(self.dependencies)

    def __str__(self) -> str:
        return f"{self.kind.title()} '{self.name}' at {self.source_file}:{self.line_start}"
