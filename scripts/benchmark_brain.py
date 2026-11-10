"""Offline synthetic measurements. Run from the repository root with Python 3.12."""
import asyncio
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.brain.skills import LocalSkills
from services.brain.store import BrainStore
from services.miko_chat_engine import MikoChatEngine
from services.miko_decision import MikoDecisionEngine
from services.miko_gate import GateDecision
from services.miko_memory import MikoMemory
from services.miko_profile import MikoProfile


def measure(fn, n):
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter()-start)*1000)
    return summarize(samples)


def summarize(samples):
    return {'n': len(samples), 'median_ms': round(statistics.median(samples), 6),
            'p95_ms': round(sorted(samples)[max(0, int(len(samples)*.95)-1)], 6)}


async def run():
    # No production credentials or models are used, including if keys are present.
    os.environ['MIKO_EXTERNAL_AI'] = os.environ['MIKO_LOCAL_AI'] = 'false'
    result = {'python':platform.python_version(), 'platform':platform.system(),
              'workload':'Sequential synthetic offline calls; transport and real models excluded'}
    decision = MikoDecisionEngine()
    gate = GateDecision(True, prompt='Can you help me debug this Python function?', reason='direct')
    result['routing'] = measure(lambda: decision.decide(gate), 1000)
    skills = LocalSkills()
    result['deterministic_calculation'] = measure(lambda: skills.calculate('calculate (8+3)*7'), 1000)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        memory = MikoMemory(root/'history.json', ephemeral=True)
        for i in range(100):
            memory.add(i, 'user', 'synthetic text', 10, i, private=True)
        result['history_lookup_100_scopes'] = measure(lambda: memory.history(1,10,1,private=True), 100)
        result['history_write_100_scopes'] = measure(lambda: memory.add(1,'assistant','synthetic reply',10,1,private=True), 100)
        store = BrainStore(root/'bot.db')
        scope = (10,20,1)
        for user in (1,2):
            for i in range(50):
                store.remember((10,20,user), f'entry_{i}', f'Synthetic Minecraft note {i}')
        result['database_preferences'] = measure(lambda: store.preferences(scope), 100)
        result['retrieval_100_documents'] = measure(lambda: store.facts(scope,'Minecraft'), 50)
        result['fts5'] = store.fts
        engine = MikoChatEngine(memory=MikoMemory(root/'engine-memory.json', ephemeral=True),
                                profile=MikoProfile(root/'profile.json'), store=store)
        engine.gate.USER_COOLDOWN = engine.gate.CHANNEL_COOLDOWN = 0
        message = NS(id=100, author=NS(id=1,bot=False,display_name='Synthetic'),guild=NS(id=10),
                     channel=NS(id=20,is_nsfw=lambda:False), content='calculate (8+3)*7', reference=None)
        bot = NS(user=NS(id=99),get_context=AsyncMock(return_value=NS(valid=False)))
        samples=[]
        errors=0
        for i in range(100):
            message.id = 100+i
            start = time.perf_counter()
            reply = await engine.ask_direct(message,bot,prompt=message.content)
            samples.append((time.perf_counter()-start)*1000)
            errors += int(reply.text != '77')
        result['end_to_end_deterministic'] = summarize(samples)
        result['provider_operations'] = dict(engine.ai.operations)
        result['error_rate'] = errors/100
        result['calculation_cache_hit_rate'] = engine.skills.hits / (engine.skills.hits+engine.skills.misses)
        result['peak_process_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result['first_token_ms'] = result['real_model_latency_ms'] = None
        await engine.close()
    path = Path(sys.argv[1]) if len(sys.argv)>1 else Path('docs/brain_benchmarks.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(run())
