"""Validate C++ code by compiling with cmake."""

import subprocess
from pathlib import Path
from typing import Optional, Tuple
from cpplib.validator.cpp_standard import CppStandard, StandardConfig


class CMakeValidator:
    """Validate C++ code by attempting to compile it with CMake."""

    def __init__(self, cmake_dir: Path, cpp_standard: Optional[CppStandard] = None):
        """
        Initialize the validator.

        Args:
            cmake_dir: Directory containing CMakeLists.txt
            cpp_standard: C++ standard to use (default: C++17)
        """
        self.cmake_dir = Path(cmake_dir)
        self.build_dir = self.cmake_dir / "build"
        self.last_output = ""
        self.last_error = ""
        self.cpp_standard = cpp_standard or StandardConfig.DEFAULT

    def validate(self, clean: bool = True, cpp_standard: Optional[CppStandard] = None) -> Tuple[bool, str]:
        """
        Validate by running cmake and building.

        Args:
            clean: Whether to clean build directory first
            cpp_standard: Override C++ standard for this build

        Returns:
            Tuple of (success: bool, output: str)
        """
        if not self.cmake_dir.exists():
            return False, f"CMake directory not found: {self.cmake_dir}"

        if not (self.cmake_dir / "CMakeLists.txt").exists():
            return False, f"CMakeLists.txt not found: {self.cmake_dir}"

        if cpp_standard is not None:
            self.cpp_standard = cpp_standard

        try:
            # Create/clean build directory
            if clean and self.build_dir.exists():
                subprocess.run(["rm", "-rf", str(self.build_dir)], check=True)

            self.build_dir.mkdir(exist_ok=True)

            # Run cmake with C++ standard
            cmake_args = [
                "cmake",
                f"-DCMAKE_CXX_STANDARD={self.cpp_standard.to_cmake_flag()}",
                "-DCMAKE_CXX_STANDARD_REQUIRED=ON",
                "..",
            ]
            result = subprocess.run(
                cmake_args,
                cwd=str(self.build_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                self.last_error = result.stderr
                self.last_output = result.stdout
                return False, f"CMake configuration failed:\n{result.stderr}"

            # Build
            result = subprocess.run(
                ["cmake", "--build", "."],
                cwd=str(self.build_dir),
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode != 0:
                self.last_error = result.stderr
                self.last_output = result.stdout
                return False, f"Build failed:\n{result.stderr}"

            self.last_output = result.stdout
            return True, f"Build successful ({self.cpp_standard.display_name})"

        except subprocess.TimeoutExpired:
            error = "Build timed out"
            self.last_error = error
            return False, error
        except Exception as e:
            error = f"Validation failed: {e}"
            self.last_error = str(e)
            return False, error

    def check_syntax(self, code: str) -> Tuple[bool, str]:
        """
        Check C++ syntax without full compilation.

        Args:
            code: C++ code to check

        Returns:
            Tuple of (valid: bool, message: str)
        """
        # Try to compile with clang/g++ directly
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".cpp", mode="w", delete=False) as f:
            f.write(code)
            temp_file = f.name

        try:
            # Try g++ with syntax-only flag
            result = subprocess.run(
                ["g++", "-fsyntax-only", temp_file],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return False, f"Syntax error:\n{result.stderr}"

            return True, "Syntax valid"

        except FileNotFoundError:
            # g++ not found, try clang
            try:
                result = subprocess.run(
                    ["clang++", "-fsyntax-only", temp_file],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode != 0:
                    return False, f"Syntax error:\n{result.stderr}"

                return True, "Syntax valid"
            except FileNotFoundError:
                return None, "No C++ compiler found (g++ or clang++)"

        except subprocess.TimeoutExpired:
            return False, "Syntax check timed out"
        except Exception as e:
            return False, f"Syntax check failed: {e}"

        finally:
            # Clean up temp file
            Path(temp_file).unlink(missing_ok=True)

    def get_build_errors(self) -> str:
        """Get the last build error output."""
        return self.last_error

    def get_build_output(self) -> str:
        """Get the last build output."""
        return self.last_output

    def is_cmake_available(self) -> bool:
        """Check if cmake is available."""
        try:
            subprocess.run(["cmake", "--version"], capture_output=True, timeout=5)
            return True
        except FileNotFoundError:
            return False

    def set_cpp_standard(self, standard: CppStandard) -> None:
        """Set the C++ standard for compilation."""
        self.cpp_standard = standard

    def get_cpp_standard(self) -> CppStandard:
        """Get the current C++ standard."""
        return self.cpp_standard

    def get_cpp_standard_display(self) -> str:
        """Get display name of current C++ standard."""
        return self.cpp_standard.display_name

    def get_supported_standards(self) -> list:
        """Get list of supported C++ standards."""
        return StandardConfig.SUPPORTED

    def check_feature_support(self, feature: str) -> bool:
        """Check if a feature is supported in the current standard."""
        return StandardConfig.has_feature(self.cpp_standard, feature)
