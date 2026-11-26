"""Optional Langfuse tracing.

Tracing is active only when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set
(LANGFUSE_BASE_URL defaults to Langfuse Cloud; set it to http://127.0.0.1:3100 for
the local server). Without them every helper here is a no-op, so the agent runs
exactly as before and needs no Langfuse server.

One question produces one trace:

  regassist-query (agent)              question in, rendered answer out, session = conversation ID
    resolve_context / planner / executor / critic / finish   (one span per graph node)
      qwen.planner, qwen.executor, ... (generation: payload, raw output, tokens, latency)
      tool.search_documents, ...       (retriever/tool: arguments in, result summary out)
      critic.verdict                   (event: decision, checks and the exact issues)
      control.stop                     (event, WARNING: why the agent abstained)
"""
from __future__ import annotations

import functools
import logging
import os
from contextlib import contextmanager, nullcontext
from typing import Any, Iterator

log = logging.getLogger(__name__)

MAX_TEXT = 4000  # characters kept per field in the dashboard

_client = None
_failed = False
_project_lookup_failed = False


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


class _NoOp:
    """Stands in for a Langfuse observation when tracing is off."""

    def update(self, **_: Any) -> "_NoOp":
        return self

    def create_event(self, **_: Any) -> None:
        return None

    def set_trace_as_public(self) -> "_NoOp":
        return self


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")) and not _failed


def client():
    """The Langfuse client, created on first use. Returns None when tracing is off."""
    global _client, _failed
    if not enabled():
        return None
    if _client is None:
        try:
            from langfuse import get_client
            _client = get_client()
        except Exception as exc:  # missing package or bad configuration must not break answers
            log.warning("Langfuse tracing disabled: %s", exc)
            _failed = True
            return None
    return _client


def set_client(value) -> None:
    """Tests: inject a client (for example one with an in-memory span exporter)."""
    global _client, _failed, _project_lookup_failed
    _client, _failed, _project_lookup_failed = value, False, False


def public_traces() -> bool:
    """LANGFUSE_PUBLIC_TRACES=1: anyone with a trace's link can view it, without logging in.
    Use only for showcase or submission runs; the trace includes the question and answer."""
    return enabled() and _flag("LANGFUSE_PUBLIC_TRACES")


def print_trace_urls() -> bool:
    """Print each trace's link in the agent's terminal (on by default with public traces)."""
    return enabled() and (_flag("LANGFUSE_PRINT_TRACE_URLS") or public_traces())


def trace_url(trace_id: str | None) -> str | None:
    """The Langfuse UI link for a trace. The first call looks up the project ID once;
    if Langfuse is unreachable it gives up for the rest of the run instead of retrying."""
    global _project_lookup_failed
    lf = client()
    if lf is None or not trace_id or _project_lookup_failed:
        return None
    try:
        return lf.get_trace_url(trace_id=trace_id)
    except Exception as exc:
        log.warning("Could not build Langfuse trace links: %s", exc)
        _project_lookup_failed = True
        return None


def clip(value: Any, limit: int = MAX_TEXT) -> Any:
    """Keep trace payloads readable and bounded."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… [{len(value) - limit} more characters]"
    if isinstance(value, dict):
        return {k: clip(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clip(v, limit) for v in value[:50]]
    return value


@contextmanager
def observation(name: str, as_type: str = "span", **fields: Any) -> Iterator[Any]:
    """Open a nested observation; yields an object with .update(...)."""
    lf = client()
    if lf is None:
        yield _NoOp()
        return
    for key in ("input", "output", "metadata"):
        if key in fields:
            fields[key] = clip(fields[key])
    with lf.start_as_current_observation(name=name, as_type=as_type, **fields) as obs:
        yield obs


def event(name: str, *, level: str = "DEFAULT", **fields: Any) -> None:
    """Record a point-in-time event under the current observation."""
    lf = client()
    if lf is None:
        return
    for key in ("input", "output", "metadata"):
        if key in fields:
            fields[key] = clip(fields[key])
    try:
        lf.create_event(name=name, level=level, **fields)
    except Exception as exc:  # tracing must never break an answer
        log.debug("Langfuse event failed: %s", exc)


@contextmanager
def new_trace() -> Iterator[None]:
    """Start the next observation as a trace root.

    Web frameworks (recent FastAPI versions included) may open their own
    OpenTelemetry span around each request. Langfuse does not receive those spans,
    so a question trace nested under one would point at a parent that never arrives.
    """
    if client() is None:
        yield
        return
    from opentelemetry import context as otel_context
    token = otel_context.attach(otel_context.Context())
    try:
        yield
    finally:
        otel_context.detach(token)


def trace_attributes(**attributes: Any):
    """Attributes (session_id, tags, metadata, trace_name) for every observation in the block."""
    if client() is None:
        return nullcontext()
    from langfuse import propagate_attributes
    return propagate_attributes(**{k: v for k, v in attributes.items() if v is not None})


def current_trace_id() -> str | None:
    lf = client()
    return lf.get_current_trace_id() if lf is not None else None


def traced_node(name: str, fn, as_type: str = "chain"):
    """Wrap a LangGraph node so each run appears as one span."""
    @functools.wraps(fn)
    def wrapper(state):
        with observation(name, as_type=as_type, metadata={"step": state.get("steps"),
                                                          "revision": state.get("revision")}) as span:
            result = fn(state)
            span.update(metadata={"route": result.get("route"), "status": result.get("status"),
                                  "steps": result.get("steps"), "revision": result.get("revision")})
            return result
    return wrapper


def summarise_tool_result(name: str, data: dict | None) -> Any:
    """Short, readable tool output for the dashboard (not whole passages)."""
    if not data:
        return data
    if name == "search_documents":
        return {"hits": data.get("hits", [])}
    if name == "fetch_passages":
        return {"passages": [
            {"citation_id": p.get("citation_id"), "section": p.get("section"), "page": p.get("page"),
             "text": (p.get("text") or "")[:300]}
            for p in data.get("passages", [])]}
    if name == "lookup_document_status":
        return {"documents": [{"document_id": d.get("document_id"), "status": d.get("status"),
                               "reason": (d.get("reason") or "")[:200]} for d in data.get("documents", [])]}
    return data


def flush() -> None:
    lf = client()
    if lf is not None:
        try:
            lf.flush()
        except Exception as exc:
            log.debug("Langfuse flush failed: %s", exc)


def shutdown() -> None:
    lf = client()
    if lf is not None:
        try:
            lf.shutdown()
        except Exception as exc:
            log.debug("Langfuse shutdown failed: %s", exc)
