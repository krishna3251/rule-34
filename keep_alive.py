from datetime import datetime, timezone
from threading import Thread
import os

from flask import Flask

app = Flask(__name__)
_bot = None


@app.route('/')
def home():
    return 'Rule43 health service'


@app.route('/health')
def health():
    connected = bool(_bot and _bot.is_ready() and not _bot.is_closed())
    failures = list(getattr(_bot, 'failed_extensions', [])) if _bot else []
    healthy = connected and not failures
    return {'status': 'healthy' if healthy else 'unavailable',
            'discord_ready': connected, 'failed_extensions': failures,
            'timestamp': datetime.now(timezone.utc).isoformat()}, 200 if healthy else 503


def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), use_reloader=False)


def keep_alive(bot=None):
    global _bot
    _bot = bot
    thread = Thread(target=run, daemon=True)
    thread.start()
    return thread


if __name__ == '__main__':
    run()
