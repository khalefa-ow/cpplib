"""Setup configuration for cpplib."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="cpplib",
    version="0.1.0",
    author="khalefaow",
    description="C++ Code Generation & Manipulation Library",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "tree-sitter>=0.21.1",
        "tree-sitter-cpp>=0.21.3",
        "pydantic>=2.5.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.3",
            "pytest-cov>=4.1.0",
            "black>=23.12.0",
            "flake8>=6.1.0",
            "mypy>=1.7.1",
        ],
        # LLM workflow layer (the `agent` package). Kept optional so that
        # cpplib itself stays a dependency-light tree-sitter library.
        "agent": [
            "dspy>=3.1,<4",
            "duckdb>=1.1",
            "pyarrow>=17",
        ],
        # Optional observability backends for the agent package. Never
        # imported at module scope, so these stay genuinely optional.
        "agent-trace": [
            "weave",
            "wandb",
        ],
    },
)
