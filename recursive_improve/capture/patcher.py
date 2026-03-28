"""Monkey-patch LLM clients to capture traces automatically.

Uses contextvars so patches are zero-cost when no session is active.
All supported providers are patched. Nested provider calls triggered from
litellm are suppressed to avoid double-logging.
"""

import contextvars
import functools

_current_session = contextvars.ContextVar("_current_session", default=None)
_nested_litellm_call = contextvars.ContextVar("_nested_litellm_call", default=False)
_patched = False


def apply_patches():
    """Patch available LLM clients. Idempotent."""
    global _patched
    if _patched:
        return
    _patched = True

    _try_patch_litellm()
    _try_patch_openai()
    _try_patch_anthropic()


def _wrap_sync(original, provider):
    """Wrap a sync LLM call to record it if a session is active."""
    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        session = _current_session.get()
        if session is None:
            return original(*args, **kwargs)

        if provider in {"openai", "anthropic"} and _nested_litellm_call.get():
            return original(*args, **kwargs)

        token = None
        if provider == "litellm":
            token = _nested_litellm_call.set(True)
        try:
            response = original(*args, **kwargs)
        finally:
            if token is not None:
                _nested_litellm_call.reset(token)

        session._record_llm_call(provider, kwargs, response)
        return response
    return wrapper


def _wrap_async(original, provider):
    """Wrap an async LLM call to record it if a session is active."""
    @functools.wraps(original)
    async def wrapper(*args, **kwargs):
        session = _current_session.get()
        if session is None:
            return await original(*args, **kwargs)

        if provider in {"openai", "anthropic"} and _nested_litellm_call.get():
            return await original(*args, **kwargs)

        token = None
        if provider == "litellm":
            token = _nested_litellm_call.set(True)
        try:
            response = await original(*args, **kwargs)
        finally:
            if token is not None:
                _nested_litellm_call.reset(token)

        session._record_llm_call(provider, kwargs, response)
        return response
    return wrapper


def _try_patch_litellm() -> bool:
    """Patch litellm.completion + litellm.acompletion if available."""
    try:
        import litellm
    except ImportError:
        return False

    litellm.completion = _wrap_sync(litellm.completion, "litellm")
    litellm.acompletion = _wrap_async(litellm.acompletion, "litellm")
    return True


def _try_patch_openai() -> bool:
    """Patch openai sync + async Completions.create if available."""
    try:
        import openai.resources.chat.completions as mod
    except ImportError:
        return False

    mod.Completions.create = _wrap_sync(mod.Completions.create, "openai")
    mod.AsyncCompletions.create = _wrap_async(mod.AsyncCompletions.create, "openai")
    return True


def _try_patch_anthropic() -> bool:
    """Patch anthropic sync + async Messages.create if available."""
    try:
        import anthropic.resources.messages as mod
    except ImportError:
        return False

    mod.Messages.create = _wrap_sync(mod.Messages.create, "anthropic")
    mod.AsyncMessages.create = _wrap_async(mod.AsyncMessages.create, "anthropic")
    return True
