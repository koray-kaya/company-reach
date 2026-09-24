"""The concurrency caps must survive more than one event loop per process.

Found in a fresh clone running the evaluations: one test's `asyncio.run`
left the cached semaphore bound to its loop, and the next test that had to
wait on it raised "bound to a different event loop". A CLI command runs one
loop, so production never met it — until anything ran two.
"""

import asyncio

import pytest

from company_reach.tools.llm import _get_semaphore
from company_reach.tools.search import _gate


async def _contend(get) -> None:
    """Make a task wait on the semaphore, which is what binds it to a loop."""
    gate = get(1)
    async with gate:
        waiter = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0)
    await waiter
    gate.release()


@pytest.mark.parametrize("get", [_get_semaphore, _gate], ids=["llm", "search"])
def test_a_second_event_loop_gets_a_semaphore_of_its_own(get):
    asyncio.run(_contend(get))
    asyncio.run(_contend(get))  # raised RuntimeError before the fix
