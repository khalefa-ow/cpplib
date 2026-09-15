"""C++ standard configuration and detection."""

from enum import Enum
from typing import Optional


class CppStandard(Enum):
    """Supported C++ standards."""

    CXX98 = ("98", "C++98")
    CXX11 = ("11", "C++11")
    CXX14 = ("14", "C++14")
    CXX17 = ("17", "C++17")
    CXX20 = ("20", "C++20")
    CXX23 = ("23", "C++23")

    def __init__(self, std_num: str, display_name: str):
        self.std_num = std_num
        self.display_name = display_name

    def to_cmake_flag(self) -> str:
        """Get the CMAKE_CXX_STANDARD value."""
        return self.std_num

    def to_compiler_flag(self) -> str:
        """Get the compiler flag (e.g., -std=c++20)."""
        return f"-std=c++{self.std_num}"

    @staticmethod
    def from_string(std_str: str) -> Optional["CppStandard"]:
        """Parse standard from string."""
        std_str = std_str.lower().replace("c++", "").replace("std", "")
        for standard in CppStandard:
            if standard.std_num == std_str:
                return standard
        return None

    def __str__(self) -> str:
        return self.display_name


class StandardConfig:
    """Configuration for C++ standard support."""

    # Default standard
    DEFAULT = CppStandard.CXX17

    # Supported standards
    SUPPORTED = [
        CppStandard.CXX98,
        CppStandard.CXX11,
        CppStandard.CXX14,
        CppStandard.CXX17,
        CppStandard.CXX20,
        CppStandard.CXX23,
    ]

    # Features available per standard
    FEATURES = {
        CppStandard.CXX98: {
            "templates": True,
            "namespaces": True,
            "exceptions": True,
        },
        CppStandard.CXX11: {
            "auto": True,
            "range_for": True,
            "lambda": True,
            "nullptr": True,
            "move_semantics": True,
            "constexpr": True,
        },
        CppStandard.CXX14: {
            "auto_return_type": True,
            "generic_lambda": True,
            "constexpr_functions": True,
            "variable_templates": True,
        },
        CppStandard.CXX17: {
            "structured_bindings": True,
            "fold_expressions": True,
            "if_constexpr": True,
            "optional": True,
            "variant": True,
            "string_view": True,
            "filesystem": True,
        },
        CppStandard.CXX20: {
            "concepts": True,
            "ranges": True,
            "coroutines": True,
            "modules": True,
            "spaceship_operator": True,
            "designated_initializers": True,
            "consteval": True,
            "requires": True,
        },
        CppStandard.CXX23: {
            "deducing_this": True,
            "explicit_this": True,
            "multi_dimensional_subscript": True,
            "literal_strings": True,
            "range_adapters": True,
            "views": True,
            "std_format": True,
        },
    }

    @staticmethod
    def get_features(standard: CppStandard) -> dict:
        """Get all features available in a standard and earlier."""
        features = {}
        for std in StandardConfig.SUPPORTED:
            features.update(StandardConfig.FEATURES.get(std, {}))
            if std == standard:
                break
        return features

    @staticmethod
    def has_feature(standard: CppStandard, feature: str) -> bool:
        """Check if a feature is available in a standard."""
        features = StandardConfig.get_features(standard)
        return features.get(feature, False)

    @staticmethod
    def get_supported_flags() -> list:
        """Get all supported standard flags."""
        return [std.to_cmake_flag() for std in StandardConfig.SUPPORTED]

    @staticmethod
    def get_supported_display() -> str:
        """Get formatted list of supported standards."""
        return ", ".join([std.display_name for std in StandardConfig.SUPPORTED])


# Convenient constants
CXX98 = CppStandard.CXX98
CXX11 = CppStandard.CXX11
CXX14 = CppStandard.CXX14
CXX17 = CppStandard.CXX17
CXX20 = CppStandard.CXX20
CXX23 = CppStandard.CXX23
