"""Small JSON result cache, validated against every source file's metadata."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pandas as pd

# Bump when accounting or result semantics change.
RESULT_CACHE_VERSION = "v3.06-1"
FRAME_NAMES = ("tubes", "monthly", "issues", "files", "settings", "channels")


def query_key(root, config):
    from dataclasses import asdict
    value = [RESULT_CACHE_VERSION, str(Path(root).resolve()), asdict(config)]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def source_fingerprint(paths):
    digest = hashlib.sha256()
    try:
        for path in paths:
            stat = path.stat()
            digest.update(json.dumps([str(path.resolve()), stat.st_size, stat.st_mtime_ns,
                                      stat.st_ctime_ns]).encode("utf-8"))
    except OSError:
        return None
    return digest.hexdigest()


def selection_fingerprint(paths, identities, rejected_names):
    """Only stat selected files; other months' contents cannot change this result."""
    fingerprint = source_fingerprint(paths)
    if fingerprint is None:
        return None
    payload = [fingerprint, sorted(identities), rejected_names]
    return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()


def load_result(cache_path, key, fingerprint):
    if cache_path is None or fingerprint is None or not Path(cache_path).is_file():
        return None
    try:
        with closing(sqlite3.connect(str(cache_path), timeout=1)) as connection:
            row = connection.execute("SELECT payload FROM gas_results_v1 WHERE query_key=? AND fingerprint=?",
                                     (key, fingerprint)).fetchone()
        if row is None:
            return None
        raw = json.loads(row[0])
        return {name: pd.DataFrame(raw[name]["data"], columns=raw[name]["columns"]).astype(raw[name]["dtypes"])
                for name in FRAME_NAMES}
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return None


def save_result(cache_path, key, fingerprint, result):
    if cache_path is None or fingerprint is None:
        return
    try:
        raw = {}
        for name in FRAME_NAMES:
            frame = getattr(result, name)
            raw[name] = {"columns": list(frame.columns), "data": frame.values.tolist(),
                         "dtypes": frame.dtypes.astype(str).to_dict()}
        # Python JSON preserves float round trips; NaN represents unavailable values.
        payload = json.dumps(raw, ensure_ascii=False)
        if len(payload.encode("utf-8")) > 16 * 1024 * 1024:
            return
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(cache_path), timeout=1)) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS gas_results_v1 (
                query_key TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT, updated INTEGER)""")
            connection.execute("INSERT OR REPLACE INTO gas_results_v1 VALUES (?, ?, ?, ?)",
                               (key, fingerprint, payload, time.time_ns()))
            connection.execute("DELETE FROM gas_results_v1 WHERE query_key NOT IN "
                               "(SELECT query_key FROM gas_results_v1 ORDER BY updated DESC LIMIT 12)")
            connection.commit()
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass  # A cache failure must never prevent calculation.


def component_key(query, paths):
    return hashlib.sha256(json.dumps([query, [str(p.resolve()) for p in paths]]).encode("utf-8")).hexdigest()


def load_component(cache_path, key, fingerprint):
    if cache_path is None or fingerprint is None or not Path(cache_path).is_file():
        return None
    try:
        with closing(sqlite3.connect(str(cache_path), timeout=1)) as connection:
            row = connection.execute("SELECT payload FROM gas_components_v1 WHERE component_key=? AND fingerprint=?",
                                     (key, fingerprint)).fetchone()
        if row is None:
            return None
        result = json.loads(row[0])
        if set(result) != {"totals", "records", "issues"}:
            return None
        return result
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return None


def save_component(cache_path, key, fingerprint, result):
    if cache_path is None or fingerprint is None:
        return
    try:
        payload = json.dumps(result, ensure_ascii=False)
        if len(payload.encode("utf-8")) > 1024 * 1024:
            return
        with closing(sqlite3.connect(str(cache_path), timeout=1)) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS gas_components_v1 (
                component_key TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT, updated INTEGER)""")
            connection.execute("INSERT OR REPLACE INTO gas_components_v1 VALUES (?, ?, ?, ?)",
                               (key, fingerprint, payload, time.time_ns()))
            connection.execute("DELETE FROM gas_components_v1 WHERE component_key NOT IN "
                               "(SELECT component_key FROM gas_components_v1 ORDER BY updated DESC LIMIT 2048)")
            connection.commit()
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass
