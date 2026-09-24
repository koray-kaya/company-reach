"""Concurrency caps, one per event loop.

`llm.ask` and `search.search` each cap how many requests are in flight. An
`asyncio.Semaphore` binds itself to the event loop it is first waited on in,
so a cap cached for the whole process broke the second time anything ran a
new loop — found in a fresh clone, where each evaluation test runs its own.
So the cap is kept per running loop, and a finished loop's caps go with it
(`WeakKeyDictionary`).
"""

import asyncio
import weakref

_by_loop: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[str, int], asyncio.Semaphore]
] = weakref.WeakKeyDictionary()


def gate(name: str, concurrency: int) -> asyncio.Semaphore:
    """The cap called `name` for the running loop. Different names never
    share a semaphore: the model and search are limited separately."""
    caps = _by_loop.setdefault(asyncio.get_running_loop(), {})
    key = (name, concurrency)
    if key not in caps:
        caps[key] = asyncio.Semaphore(concurrency)
    return caps[key]
