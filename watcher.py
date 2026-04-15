import json
import os
import sys
import time
import uuid
from pathlib import Path


def app_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_PATH = app_dir() / 'config.json'
STATE_PATH = app_dir() / 'state.json'


def load_json(path, default):
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config():
    if not CONFIG_PATH.exists():
        raise FileNotFoundError('Missing config.json. Copy config.example.json to config.json and edit paths.')
    return load_json(CONFIG_PATH, {})


def ensure_bridge_files(bridge_dir):
    bridge_path = Path(bridge_dir)
    bridge_path.mkdir(parents=True, exist_ok=True)
    queue_path = bridge_path / 'jobs.json'
    config_path = bridge_path / 'bridge_config.json'
    if not queue_path.exists():
        save_json(queue_path, {"jobs": []})
    return bridge_path, queue_path, config_path


def write_bridge_config(config_path, queue_path, poll_interval_seconds, defaults):
    payload = {
        "queue_file": str(queue_path),
        "poll_interval_ms": int(poll_interval_seconds) * 1000,
        "job_defaults": defaults or {}
    }
    save_json(config_path, payload)


def is_stable(file_path, checks, wait_seconds):
    previous_size = None
    unchanged = 0
    for _ in range(checks):
        try:
            current_size = os.path.getsize(file_path)
        except OSError:
            return False
        if previous_size is not None and current_size == previous_size:
            unchanged += 1
        previous_size = current_size
        time.sleep(wait_seconds)
    return unchanged >= max(1, checks - 1)


def scan_files(root, allowed_exts):
    items = []
    root_path = Path(root)
    for path in root_path.rglob('*'):
        if path.is_file() and path.suffix.lower() in allowed_exts:
            items.append(path)
    return items


def append_job(queue_path, job):
    payload = load_json(queue_path, {"jobs": []})
    payload.setdefault('jobs', []).append(job)
    save_json(queue_path, payload)


def file_signature(path_obj):
    stat = path_obj.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns
    }


def should_queue(file_key, signature, files_state):
    existing = files_state.get(file_key)
    if not existing:
        return True
    return existing.get('size') != signature['size'] or existing.get('mtime_ns') != signature['mtime_ns']


def mark_file(files_state, file_key, signature, status):
    files_state[file_key] = {
        "size": signature['size'],
        "mtime_ns": signature['mtime_ns'],
        "status": status,
        "updated_at": int(time.time())
    }


def main():
    config = load_config()
    bridge_dir, queue_path, bridge_config_path = ensure_bridge_files(config['bridge_dir'])
    watch_folder = Path(config['watch_folder'])
    allowed_exts = {ext.lower() for ext in config.get('allowed_extensions', [])}
    poll_interval = int(config.get('poll_interval_seconds', 3))
    stable_checks = int(config.get('stable_checks', 3))
    stable_wait_seconds = int(config.get('stable_wait_seconds', 2))
    defaults = config.get('job_defaults', {})

    write_bridge_config(bridge_config_path, queue_path, poll_interval, defaults)

    state = load_json(STATE_PATH, {"files": {}})
    files_state = state.setdefault('files', {})

    print(f'Watching: {watch_folder}')
    print(f'Bridge dir: {bridge_dir}')
    print(f'Queue: {queue_path}')

    while True:
        try:
            files = scan_files(watch_folder, allowed_exts)
            for file_path in files:
                key = str(file_path.resolve())
                signature = file_signature(file_path)
                if not should_queue(key, signature, files_state):
                    continue
                if not is_stable(file_path, stable_checks, stable_wait_seconds):
                    continue
                job = {
                    "id": str(uuid.uuid4()),
                    "status": "queued",
                    "created_at": int(time.time()),
                    "path": key,
                    "name": file_path.name,
                    "type": "asset",
                    "mode": defaults.get('mode', 'append'),
                    "video_track": defaults.get('video_track', 0),
                    "audio_track": defaults.get('audio_track', 0),
                    "target_sequence": defaults.get('target_sequence', 'ACTIVE')
                }
                append_job(queue_path, job)
                mark_file(files_state, key, signature, 'queued')
                save_json(STATE_PATH, state)
                write_bridge_config(bridge_config_path, queue_path, poll_interval, defaults)
                print(f'Queued: {file_path.name}')
        except Exception as exc:
            print(f'Watcher error: {exc}')
        time.sleep(poll_interval)


if __name__ == '__main__':
    main()
