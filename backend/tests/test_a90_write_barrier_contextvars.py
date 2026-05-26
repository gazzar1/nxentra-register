# tests/test_a90_write_barrier_contextvars.py
"""
A95 (Track-2 chat name: A90) — pin the write-barrier's stack semantics
under contextvars.

The existing test_write_barrier.py covers model-save behavior end-to-end.
This file pins the primitive contract:

  - empty default
  - LIFO push/pop on enter/exit
  - exception-safety (pop on raise)
  - thread isolation (two threads see separate stacks)
  - asyncio.create_task INHERITS the parent's stack
  - asyncio.create_task mutations don't leak back to the parent

The async cases are the reason we swapped threading.local → ContextVar.
A threading-local stack survives across `await` (single thread), but
the moment a coroutine is scheduled as a separate task, asyncio still
runs it on the same thread — and a thread-local can either share state
across concurrent tasks (causing leakage) or sit empty (causing missed
context), depending on subtle scheduling. ContextVar makes the
inheritance + isolation contract explicit and testable.
"""

import asyncio
import threading

import pytest

from projections.write_barrier import (
    _write_context_stack,
    admin_emergency_writes_allowed,
    auth_writes_allowed,
    bootstrap_writes_allowed,
    command_writes_allowed,
    current_write_context,
    migration_writes_allowed,
    projection_writes_allowed,
    write_context_allowed,
)

# ============================================================================
# Sync semantics — stack discipline
# ============================================================================


def test_default_context_is_none():
    assert current_write_context() is None


def test_command_context_pushes_command():
    with command_writes_allowed():
        assert current_write_context() == "command"
    assert current_write_context() is None


def test_each_helper_pushes_its_named_context():
    cases = [
        (command_writes_allowed, "command"),
        (auth_writes_allowed, "auth"),
        (projection_writes_allowed, "projection"),
        (migration_writes_allowed, "migration"),
        (bootstrap_writes_allowed, "bootstrap"),
    ]
    for helper, expected in cases:
        with helper():
            assert current_write_context() == expected, (
                f"{helper.__name__} should push '{expected}', got {current_write_context()!r}"
            )
        assert current_write_context() is None, f"{helper.__name__} leaked context after exit"


def test_nested_contexts_are_lifo():
    with command_writes_allowed():
        assert current_write_context() == "command"
        with projection_writes_allowed():
            assert current_write_context() == "projection"
            with bootstrap_writes_allowed():
                assert current_write_context() == "bootstrap"
            assert current_write_context() == "projection"
        assert current_write_context() == "command"
    assert current_write_context() is None


def test_context_pops_on_exception():
    with pytest.raises(ValueError):
        with command_writes_allowed():
            assert current_write_context() == "command"
            raise ValueError("boom")
    # The contextmanager's finally must have reset the token.
    assert current_write_context() is None


def test_write_context_allowed_matches_active_context():
    assert write_context_allowed({"command"}) is False  # nothing active

    with command_writes_allowed():
        assert write_context_allowed({"command"}) is True
        assert write_context_allowed({"projection"}) is False
        assert write_context_allowed({"command", "projection"}) is True

    assert write_context_allowed({"command"}) is False


def test_admin_emergency_requires_explicit_setting(settings):
    settings.ALLOW_ADMIN_EMERGENCY_WRITES = False
    with pytest.raises(RuntimeError, match="admin_emergency writes are disabled"):
        with admin_emergency_writes_allowed():
            pass  # Should never reach.

    settings.ALLOW_ADMIN_EMERGENCY_WRITES = True
    with admin_emergency_writes_allowed():
        assert current_write_context() == "admin_emergency"
        # write_context_allowed also gates on the setting being on.
        assert write_context_allowed({"admin_emergency"}) is True

    settings.ALLOW_ADMIN_EMERGENCY_WRITES = False


# ============================================================================
# Thread isolation
# ============================================================================


def test_two_threads_have_independent_stacks():
    """Threading isolation is the floor we had under threading.local — must
    survive the switch to ContextVar so background workers / Celery / RQ
    don't see each other's barriers.
    """
    results: dict[str, str | None] = {}
    parent_seen: list[str | None] = []
    barrier = threading.Event()

    def worker():
        # Worker thread sees its own (empty) context.
        results["thread_default"] = current_write_context()
        with projection_writes_allowed():
            results["thread_inside_projection"] = current_write_context()
        barrier.set()

    with command_writes_allowed():
        parent_seen.append(current_write_context())  # "command"
        t = threading.Thread(target=worker)
        t.start()
        barrier.wait(timeout=2)
        t.join()
        parent_seen.append(current_write_context())  # still "command"

    assert parent_seen == ["command", "command"]
    # Worker thread started with default (None), saw its own projection ctx.
    assert results["thread_default"] is None
    assert results["thread_inside_projection"] == "projection"


# ============================================================================
# Asyncio inheritance + isolation (the whole reason we did A90)
# ============================================================================


def test_asyncio_task_inherits_parent_write_context():
    """When the codebase grows an async surface (Channels consumer, async
    management command, AI/agent worker), a task created inside
    `command_writes_allowed()` must still see the active context — or
    finance writes inside the task will be rejected by ProjectionWriteManager
    as "no context active".

    threading.local lost this on create_task. ContextVar preserves it
    because Python copies the current Context into the new task.
    """
    captured: dict[str, str | None] = {}

    async def child_task():
        captured["inside_task"] = current_write_context()

    async def parent():
        with command_writes_allowed():
            captured["before_spawn"] = current_write_context()
            await asyncio.create_task(child_task())
            captured["after_spawn"] = current_write_context()

    asyncio.run(parent())

    assert captured["before_spawn"] == "command"
    assert captured["inside_task"] == "command", (
        "Task did NOT inherit the parent's write context. ContextVar "
        "propagation is broken — finance writes in async workers will "
        "be silently rejected."
    )
    assert captured["after_spawn"] == "command"


def test_asyncio_task_context_change_does_not_leak_to_parent():
    """A task may push its own write context, but mutations must NOT
    leak back when the task completes. Otherwise concurrent tasks would
    corrupt each other's barriers.
    """
    captured: dict[str, str | None] = {}

    async def child_task():
        with projection_writes_allowed():
            captured["inside_task"] = current_write_context()

    async def parent():
        with command_writes_allowed():
            captured["before_spawn"] = current_write_context()
            await asyncio.create_task(child_task())
            # Parent must STILL be in command — task's projection push
            # must not have leaked back.
            captured["after_spawn"] = current_write_context()

    asyncio.run(parent())

    assert captured["before_spawn"] == "command"
    assert captured["inside_task"] == "projection"
    assert captured["after_spawn"] == "command", (
        "Task's projection_writes_allowed leaked back into the parent's context. ContextVar isolation is broken."
    )


def test_concurrent_asyncio_tasks_each_keep_their_own_context():
    """Two tasks started under the same parent context can independently
    push different sub-contexts without crossing wires. This is the
    real-world scenario for multi-agent reconciliation workers running
    in parallel.
    """
    captured: dict[int, list[str | None]] = {0: [], 1: []}

    async def worker(idx: int, helper):
        captured[idx].append(current_write_context())  # inherits parent
        with helper():
            await asyncio.sleep(0)  # force a yield so tasks interleave
            captured[idx].append(current_write_context())
        captured[idx].append(current_write_context())  # back to parent

    async def parent():
        with command_writes_allowed():
            await asyncio.gather(
                worker(0, projection_writes_allowed),
                worker(1, bootstrap_writes_allowed),
            )

    asyncio.run(parent())

    assert captured[0] == ["command", "projection", "command"], captured[0]
    assert captured[1] == ["command", "bootstrap", "command"], captured[1]


# ============================================================================
# Sanity: the underlying ContextVar default is empty tuple, not None
# ============================================================================


def test_underlying_contextvar_default_is_empty_tuple():
    """An implementation detail worth pinning: the ContextVar's default
    must be the empty tuple `()`, not `None`. If anyone "fixes" the
    default to `None`, `current_write_context()` will TypeError on
    subscripting None instead of returning None cleanly.
    """
    # Reset_token-free read of the default.
    assert _write_context_stack.get() == ()
