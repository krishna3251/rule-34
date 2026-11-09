"""Admission, deduplication and cancellation, with no unbounded waiting queue."""
import asyncio
from collections import Counter, OrderedDict, deque
from time import monotonic


class BrainRuntime:
    def __init__(self, config):
        self.config = config
        self.active = {}
        self.blocked_users = Counter()
        self.seen = OrderedDict()
        self.conversations = OrderedDict()
        self.closed = False
        self.metrics = Counter()
        self.latencies = deque(maxlen=200)

    async def run(self, key, event_id, operation, busy):
        now = monotonic()
        while self.seen and next(iter(self.seen.values())) < now - 120:
            self.seen.popitem(last=False)
        if event_id is not None and event_id in self.seen:
            self.metrics['duplicate'] += 1
            return busy('duplicate')
        guild, channel, user = key
        overloaded = (self.closed or user in self.blocked_users or key in self.active or len(self.active) >= self.config.max_active
                      or sum(k[0] == guild for k in self.active) >= self.config.guild_active
                      or sum(k[0] == guild and k[2] == user for k in self.active) >= self.config.user_active)
        if overloaded:
            self.metrics['busy'] += 1
            return busy('busy')
        if event_id is not None:
            self.seen[event_id] = now
            while len(self.seen) > 2048:
                self.seen.popitem(last=False)
        task = asyncio.current_task()
        self.active[key] = task
        self.metrics['requests'] += 1
        try:
            async with asyncio.timeout(self.config.timeout):
                result = await operation()
            self.metrics['completed'] += 1
            return result
        except TimeoutError:
            self.metrics['timeout'] += 1
            return busy('timeout')
        except asyncio.CancelledError:
            self.metrics['cancelled'] += 1
            raise
        finally:
            self.active.pop(key, None)
            self.latencies.append((monotonic() - now) * 1000)

    async def cancel(self, user_id, guild_id=None, channel_id=None):
        tasks = [task for key, task in self.active.items()
                 if key[2] == user_id and (guild_id is None or key[0] == guild_id)
                 and (channel_id is None or key[1] == channel_id)
                 and task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    async def close(self):
        self.closed = True
        tasks = [t for t in self.active.values() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def snapshot(self):
        samples = sorted(self.latencies)
        return {'active': len(self.active), **self.metrics,
                'p95_ms': samples[max(0, int(len(samples)*.95)-1)] if samples else None}


async def worker(function, *args, **kwargs):
    """Drain a started SQLite/JSON worker before releasing its request ownership."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                break
        except Exception:
            if cancelled:
                raise asyncio.CancelledError
            raise
    if cancelled:
        # Retrieve exceptions without replacing cancellation or leaving work behind.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return result
