"""
LangSmith tracing integration.

How it works:
  1. setup_langsmith() enables the LiteLLM → LangSmith callback so every
     model call (across all agents) is logged as a child span automatically.
  2. @trace_review wraps run_review() as the root trace — all LiteLLM spans
     nest under it via LangSmith's context propagation.
  3. If LANGSMITH_API_KEY is absent, everything degrades gracefully to a no-op.

Required .env vars:
  LANGSMITH_API_KEY   = ls_...
  LANGSMITH_PROJECT   = codereview-agent   (optional, defaults shown)
"""

import os
from functools import wraps
from typing import Callable

_ENABLED = False


def setup_langsmith() -> bool:
    """
    Configure LiteLLM to forward every completion to LangSmith.
    Call once at startup before any agent runs.
    Returns True if LangSmith was successfully enabled.
    """
    global _ENABLED

    api_key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    if not api_key:
        print("[tracing] LANGSMITH_API_KEY not set — tracing disabled.")
        return False

    try:
        import litellm

        # LiteLLM reads LANGCHAIN_API_KEY / LANGCHAIN_PROJECT automatically
        os.environ.setdefault("LANGCHAIN_API_KEY", api_key)
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault(
            "LANGCHAIN_PROJECT",
            os.environ.get("LANGSMITH_PROJECT", "codereview-agent"),
        )

        if "langsmith" not in litellm.success_callback:
            litellm.success_callback.append("langsmith")

        _ENABLED = True
        print(
            f"[tracing] LangSmith enabled → project "
            f"'{os.environ['LANGCHAIN_PROJECT']}'"
        )
        return True

    except Exception as exc:
        print(f"[tracing] LangSmith setup failed: {exc}")
        return False


def trace_review(fn: Callable) -> Callable:
    """
    Decorator that wraps an async review function as a LangSmith root trace.
    The run_id and label are logged as metadata so each trace is searchable.

    If LangSmith is not available the original function is returned unchanged.
    """
    if not _ENABLED:
        return fn

    try:
        from langsmith import traceable

        @traceable(name="code_review_pipeline", run_type="chain")
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            return await fn(*args, **kwargs)

        return wrapper

    except Exception:
        return fn


def is_enabled() -> bool:
    return _ENABLED
