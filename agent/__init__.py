"""DSPy-based workflow for generating and optimizing C++ query engines.

Stages: propose a storage plan from a schema and a query workload, divide it into
hint levels, generate the storage-layout header, generate per-query C++ and loop
until it compiles and matches gold results, then optimize with hints.

The LLM layer is DSPy (``dspy.RLM`` for long-context exploration so whole files
never enter a prompt); the C++ reading and editing is done by ``cpplib`` through
host-side tools handed to the RLM sandbox.

Only :mod:`agent.errors` and :mod:`agent.config` are imported eagerly. Everything
touching ``dspy`` is imported lazily so that ``agent.cli doctor`` can report a
missing extra instead of dying on an ImportError.
"""

__version__ = "0.1.0"

from agent.config import AgentConfig, LoadedConfig, ResolvedStageConfig, load_config
from agent.errors import AgentError, ConfigError, MissingInputError, StageNotImplemented

__all__ = [
    "AgentConfig",
    "AgentError",
    "ConfigError",
    "LoadedConfig",
    "MissingInputError",
    "ResolvedStageConfig",
    "StageNotImplemented",
    "__version__",
    "load_config",
]
