"""Small JSON result cache, validated against every source file's metadata."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pandas as pd

# Bump when accounting or result semantics change.
RESULT_CACHE_VERSION = "v3.10-1"
FRAME_NAMES = ("tubes", "monthly", "issues", "files", "settings", "channels")


def query_key(root, config):
    from dataclasses import asdict
    value = [RESULT_CACHE_VERSION, str(Path(root).resolve()), asdict(config)]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def source_stamp(path):
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except OSError:
        return None


def fingerprint_stamps(stamps):
    digest = hashlib.sha256()
    for stamp in stamps:
        if stamp is None:
            return None
        digest.update(json.dumps(stamp).encode("utf-8"))
    return digest.hexdigest()


def source_fingerprint(paths):
    return fingerprint_stamps(source_stamp(path) for path in paths)


def selection_fingerprint(paths, identities, rejected_names, *, stamps=None):
    """Only stat selected files; other months' contents cannot change this result."""
    fingerprint = (source_fingerprint(paths) if stamps is None else
                   fingerprint_stamps(stamps[p] for p in paths))
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


def component_cache_keys(query, stamps):
    fingerprint = fingerprint_stamps(stamps)
    if fingerprint is None:
        return None, None
    resolved_paths = [stamp[0] for stamp in stamps]
    key = hashlib.sha256(json.dumps([query, resolved_paths]).encode("utf-8")).hexdigest()
    return key, fingerprint


class ComponentCache:
    """Reuse the connection and persist verified components in bounded batches."""

    def __init__(self, path):
        self.path = Path(path) if path is not None else None
        self.connection = None
        self.readable = False
        self.read_checked = False
        self.disabled = self.path is None
        self.pending = []
        self.pending_bytes = 0

    def connect(self):
        if self.connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(str(self.path), timeout=1)

    def load(self, key, fingerprint):
        if self.disabled or fingerprint is None:
            return None
        if not self.read_checked:
            self.read_checked = True
            try:
                if not self.path.is_file():
                    return None
                self.connect()
                self.readable = self.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gas_components_v1'"
                ).fetchone() is not None
            except (OSError, sqlite3.Error):
                self.disabled = True
                return None
        if not self.readable:
            return None
        try:
            row = self.connection.execute(
                "SELECT payload FROM gas_components_v1 WHERE component_key=? AND fingerprint=?",
                (key, fingerprint)).fetchone()
            if row is None:
                return None
            result = json.loads(row[0])
            if set(result) == {"totals", "records", "issues"}:
                return result
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            pass
        return None

    def queue(self, key, fingerprint, result):
        if self.disabled or fingerprint is None:
            return
        try:
            payload = json.dumps(result, ensure_ascii=False)
            size = len(payload.encode("utf-8"))
        except (ValueError, TypeError):
            return
        if size > 1024 * 1024:
            return
        self.pending.append((key, fingerprint, payload, time.time_ns()))
        self.pending_bytes += size
        if len(self.pending) >= 64 or self.pending_bytes >= 4 * 1024 * 1024:
            self.flush()

    def flush(self):
        if self.disabled or not self.pending:
            return
        try:
            self.connect()
            with self.connection:
                self.connection.execute("""CREATE TABLE IF NOT EXISTS gas_components_v1 (
                    component_key TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT, updated INTEGER)""")
                self.connection.executemany("INSERT OR REPLACE INTO gas_components_v1 VALUES (?, ?, ?, ?)",
                                            self.pending)
                self.connection.execute("DELETE FROM gas_components_v1 WHERE component_key NOT IN "
                                        "(SELECT component_key FROM gas_components_v1 ORDER BY updated DESC LIMIT 2048)")
            self.readable = True
        except (OSError, sqlite3.Error):
            self.disabled = True
        finally:
            self.pending.clear()
            self.pending_bytes = 0

    def close(self):
        self.flush()
        if self.connection is not None:
            self.connection.close()
