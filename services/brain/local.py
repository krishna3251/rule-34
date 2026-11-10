"""Lazy loopback-only OpenAI-compatible transport for Ollama/llama.cpp servers."""
import asyncio
import ipaddress
import json
from urllib.parse import urlsplit

import aiohttp


class LocalModel:
    def __init__(self, config):
        self.config = config
        self.session = None
        self.semaphore = asyncio.Semaphore(1)
        parsed = urlsplit(config.local_url)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:
            loopback = False
        if (not loopback or parsed.scheme != 'http' or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path.rstrip('/') != '/v1'):
            raise ValueError('MIKO_LOCAL_URL must be http://<loopback IP>:<port>/v1')
        self.url = config.local_url.rstrip('/') + '/chat/completions'

    async def generate(self, messages, temperature):
        async with asyncio.timeout(self.config.local_timeout):
            async with self.semaphore:
                if self.session is None or self.session.closed:
                    self.session = aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=self.config.local_timeout))
                async with self.session.post(self.url, json={'model': self.config.local_model,
                        'messages': messages, 'temperature': temperature, 'max_tokens': 450, 'stream': False},
                        allow_redirects=False) as response:
                    if response.status != 200:
                        raise RuntimeError(f'Local inference HTTP {response.status}')
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        chunks.extend(chunk)
                        if len(chunks) > 131072:
                            raise ValueError('Local response exceeds limit')
                    body = json.loads(chunks)
                    message = body['choices'][0]['message']
                    if message.get('tool_calls') or not isinstance(message.get('content'), str) or not message['content'].strip():
                        raise ValueError('Local model must return a plain text answer')
                    return body

    async def close(self):
        if self.session is not None:
            await self.session.close()
