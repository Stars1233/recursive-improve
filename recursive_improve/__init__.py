"""recursive-improve: Recursively improve AI agents from their traces."""

__version__ = "0.1.0"


def patch():
    """Monkey-patch LLM clients to capture traces automatically."""
    from recursive_improve.capture.patcher import apply_patches
    apply_patches()


def session(traces_dir="./traces", session_id=None, metadata=None):
    """Create a trace capture session context manager.

    Usage:
        import recursive_improve as ri
        ri.patch()
        with ri.session("./traces") as s:
            # your agent code here
            result = client.chat.completions.create(...)
            s.finish(output=result)
    """
    from recursive_improve.capture.session import Session
    return Session(traces_dir=traces_dir, session_id=session_id, metadata=metadata)


def TracedAgent(fn, traces_dir="./traces", **session_kwargs):
    """Wrap an agent function with automatic trace capture.

    Usage:
        import recursive_improve as ri
        ri.patch()
        agent = ri.TracedAgent(my_agent_fn, "./traces")
        result = agent.run(user_input)
    """
    from recursive_improve.capture.session import TracedAgentWrapper
    return TracedAgentWrapper(fn=fn, traces_dir=traces_dir, **session_kwargs)
