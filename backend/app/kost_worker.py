"""Supervised polling worker; KOST jobs never depend on an open browser."""
import json
import os
import signal
import sys
import time
from pathlib import Path
from threading import Event
from .adapters.kost import KostClient
from .kost_validation import Config, step
from .service import Service


def heartbeat_path():
    return Path(os.getenv('DATA_DIR', 'var'))/'kost-worker.json'


def check():
    config = Config.from_env()
    try:
        value = json.loads(heartbeat_path().read_text())
        return value['configuration'] == config.fingerprint() and value['enabled'] == config.enabled and time.time()-value['time'] < 360
    except (OSError, ValueError, KeyError):
        return False


def main():
    if '--check' in sys.argv:
        return 0 if check() else 1
    config = Config.from_env()
    service = Service(Path(os.getenv('DATA_DIR', 'var')), Path(os.getenv('SCHEMA_DIR', 'schemas')),
        database_url=os.getenv('DATABASE_URL'))
    client = KostClient(config.url, config.api_key)
    stopped = Event()
    for kind in (signal.SIGINT, signal.SIGTERM):
        signal.signal(kind, lambda *_: stopped.set())
    try:
        while not stopped.is_set():
            path = heartbeat_path()
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'time': time.time(), 'enabled': config.enabled, 'configuration': config.fingerprint()}))
            temporary.replace(path)
            if config.enabled:
                step(service, client)
            stopped.wait(1)
    finally:
        client.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())