"""Modify C++ files by inserting and replacing code."""

from pathlib import Path
from typing import Optional, List, Tuple
from cpplib.pieces.code_piece import CodePiece
from cpplib.generator.code_generator import CodeGenerator


class FileModifier:
    """Modify C++ files by inserting, replacing, or removing code."""

    def __init__(self):
        """Initialize the file modifier."""
        self.generator = CodeGenerator()
        self.modifications: List[Tuple[Path, str, str]] = []  # (file, old, new)

    def insert_function(
        self,
        file_path: Path,
        piece: CodePiece,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> bool:
        """
        Insert a function into a file.

        Args:
            file_path: Path to the C++ file
            piece: CodePiece representing the function
            after: Insert after this function/marker name
            before: Insert before this function/marker name

        Returns:
            True if successful, False otherwise
        """
        if piece.kind != "function":
            raise ValueError(f"Expected function kind, got {piece.kind}")

        content = self._read_file(file_path)
        if content is None:
            return False

        # Generate the function code
        func_code = self.generator.generate_function(piece)

        # Determine insertion point
        new_content = self._insert_at_position(content, func_code, after, before)

        if new_content == content:
            print(f"Warning: Could not find insertion point in {file_path}")
            return False

        self.modifications.append((file_path, content, new_content))
        return True

    def insert_class(
        self,
        file_path: Path,
        piece: CodePiece,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> bool:
        """
        Insert a class into a file.

        Args:
            file_path: Path to the C++ file
            piece: CodePiece representing the class
            after: Insert after this class/marker name
            before: Insert before this class/marker name

        Returns:
            True if successful, False otherwise
        """
        if piece.kind not in ["class", "struct"]:
            raise ValueError(f"Expected class/struct kind, got {piece.kind}")

        content = self._read_file(file_path)
        if content is None:
            return False

        # Generate the class code
        class_code = self.generator.generate_class(piece)

        # Determine insertion point
        new_content = self._insert_at_position(content, class_code, after, before)

        if new_content == content:
            print(f"Warning: Could not find insertion point in {file_path}")
            return False

        self.modifications.append((file_path, content, new_content))
        return True

    def replace_function(
        self, file_path: Path, func_name: str, new_piece: CodePiece
    ) -> bool:
        """
        Replace an existing function with a new one.

        Args:
            file_path: Path to the C++ file
            func_name: Name of function to replace
            new_piece: New CodePiece

        Returns:
            True if successful
        """
        content = self._read_file(file_path)
        if content is None:
            return False

        # Find the function boundaries
        start_idx = self._find_function_start(content, func_name)
        if start_idx == -1:
            return False

        end_idx = self._find_function_end(content, start_idx)
        if end_idx == -1:
            return False

        # Generate new code
        new_code = self.generator.generate_function(new_piece)

        # Replace
        new_content = content[:start_idx] + new_code + content[end_idx:]
        self.modifications.append((file_path, content, new_content))
        return True

    def delete_function(self, file_path: Path, func_name: str) -> bool:
        """
        Delete a function from a file.

        Args:
            file_path: Path to the C++ file
            func_name: Name of function to delete

        Returns:
            True if successful
        """
        content = self._read_file(file_path)
        if content is None:
            return False

        start_idx = self._find_function_start(content, func_name)
        if start_idx == -1:
            return False

        end_idx = self._find_function_end(content, start_idx)
        if end_idx == -1:
            return False

        new_content = content[:start_idx] + content[end_idx:]
        self.modifications.append((file_path, content, new_content))
        return True

    def apply_modifications(self) -> bool:
        """
        Apply all pending modifications to files.

        Returns:
            True if all modifications succeeded
        """
        all_succeeded = True

        for file_path, _, new_content in self.modifications:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
            except Exception as e:
                print(f"Failed to write {file_path}: {e}")
                all_succeeded = False

        return all_succeeded

    def rollback(self) -> None:
        """Rollback all pending modifications without applying them."""
        self.modifications.clear()

    def get_pending_changes(self) -> List[Tuple[str, str]]:
        """
        Get a summary of pending changes.

        Returns:
            List of (file_path, change_description)
        """
        changes = []
        for file_path, old, new in self.modifications:
            change_type = "modified"
            if len(new) > len(old):
                change_type = f"inserted {len(new) - len(old)} bytes"
            elif len(new) < len(old):
                change_type = f"deleted {len(old) - len(new)} bytes"

            changes.append((str(file_path), change_type))

        return changes

    def _read_file(self, file_path: Path) -> Optional[str]:
        """Read a file safely."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"Failed to read {file_path}: {e}")
            return None

    def _insert_at_position(
        self,
        content: str,
        new_code: str,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> str:
        """
        Insert code at a specified position.

        Args:
            content: Original file content
            new_code: Code to insert
            after: Insert after this marker
            before: Insert before this marker

        Returns:
            Modified content
        """
        # Add newlines for proper formatting
        new_code = "\n" + new_code.strip() + "\n"

        if after:
            # Find the marker and insert after it
            idx = content.find(after)
            if idx != -1:
                # Find the end of the line
                end_idx = content.find("\n", idx)
                if end_idx != -1:
                    return content[: end_idx + 1] + new_code + content[end_idx + 1 :]

        if before:
            # Find the marker and insert before it
            idx = content.find(before)
            if idx != -1:
                return content[:idx] + new_code + content[idx:]

        # Default: insert at end
        return content + new_code

    def _find_function_start(self, content: str, func_name: str) -> int:
        """Find the start of a function definition."""
        # Simple heuristic: look for "type functionName("
        search_str = f"{func_name}("
        return content.find(search_str)

    def _find_function_end(self, content: str, start_idx: int) -> int:
        """Find the end of a function definition (closing brace)."""
        # Simple heuristic: find matching braces
        brace_count = 0
        in_function = False

        for i in range(start_idx, len(content)):
            if content[i] == "{":
                in_function = True
                brace_count += 1
            elif content[i] == "}" and in_function:
                brace_count -= 1
                if brace_count == 0:
                    return i + 1

        return -1
