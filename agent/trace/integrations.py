"""Optional weave / wandb wiring.

Both are imported inside the functions and every failure degrades to a warning.
An observability backend that is unavailable, unauthenticated or broken must
never take down a long generation run that is otherwise working.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.config.models import TraceConfig

log = logging.getLogger(__name__)


def maybe_init_weave(cfg: TraceConfig, default_project: str = "cpp-agent") -> Optional[Any]:
    """Initialize Weave tracing if ``enable_weave`` is set.

    Weave patches LiteLLM/DSPy on import, so calling this before the stage runs
    is what makes calls show up. Returns the weave module on success, else None.
    """
    if not cfg.enable_weave:
        return None
    try:
        import weave
    except ImportError:
        log.warning(
            "trace.enable_weave is set but the 'weave' package is not installed; "
            'continuing without it. Install with: uv pip install -e ".[agent-trace]"'
        )
        return None
    try:
        weave.init(cfg.weave_project_name or default_project)
        return weave
    except Exception as exc:
        log.warning("weave.init failed (%s); continuing without Weave tracing.", exc)
        return None


def maybe_init_wandb(
    cfg: TraceConfig,
    run_name: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
    default_project: str = "cpp-agent",
) -> Optional[Any]:
    """Start a wandb run if ``enable_wandb`` is set.

    Returns the wandb module on success, else None.
    """
    if not cfg.enable_wandb:
        return None
    try:
        import wandb
    except ImportError:
        log.warning(
            "trace.enable_wandb is set but the 'wandb' package is not installed; "
            'continuing without it. Install with: uv pip install -e ".[agent-trace]"'
        )
        return None
    try:
        wandb.init(
            project=cfg.wandb_project or default_project,
            name=run_name,
            config=config or {},
            reinit=True,
        )
        return wandb
    except Exception as exc:
        log.warning("wandb.init failed (%s); continuing without W&B logging.", exc)
        return None


def log_metrics(
    wandb_module: Optional[Any], metrics: dict[str, Any], step: Optional[int] = None
) -> None:
    """Log a stage's metrics to wandb, if it is active."""
    if wandb_module is None:
        return
    numeric = {k: v for k, v in metrics.items() if isinstance(v, (int, float, bool))}
    if not numeric:
        return
    try:
        wandb_module.log(numeric, step=step)
    except Exception as exc:
        log.warning("wandb.log failed (%s); continuing.", exc)


def finish_wandb(wandb_module: Optional[Any]) -> None:
    """End the wandb run, if one was started."""
    if wandb_module is None:
        return
    try:
        wandb_module.finish()
    except Exception as exc:
        log.warning("wandb.finish failed (%s); continuing.", exc)
