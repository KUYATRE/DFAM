"""Disk-backed interval ordering with a fixed SQLite page cache."""

from contextlib import closing
from itertools import count
from pathlib import Path
import shutil
import sqlite3
import tempfile

import numpy as np

CSV_CHUNK_ROWS = 10_000
SQLITE_CACHE_KIB = 8 * 1024
MIN_TEMP_FREE_BYTES = 1024 ** 3


class DiskIntervalStore:
    def __init__(self, cancelled, temp_parent=None):
        self.cancelled = cancelled
        self.temp_parent = temp_parent
        self.directory = None
        self.connection = None
        self.sequence = count()

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="DFAM-gas-", dir=self.temp_parent)
        try:
            self.check_space()
            self.connection = sqlite3.connect(str(Path(self.directory.name) / "intervals.sqlite3"))
            self.connection.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_KIB}")
            self.connection.execute("PRAGMA mmap_size=0")
            self.connection.execute("PRAGMA temp_store=FILE")
            self.connection.execute("PRAGMA synchronous=OFF")
            self.connection.execute("PRAGMA threads=1")
            # The on-disk primary key replaces an all-records in-memory sort.
            self.connection.execute("""CREATE TABLE spans (
                tube TEXT, gas TEXT, start INTEGER, file_id INTEGER, end INTEGER,
                seq INTEGER, q0 REAL, q1 REAL, channels BLOB, repeat INTEGER,
                PRIMARY KEY(tube, gas, start, repeat, file_id, end, seq)) WITHOUT ROWID""")
            self.connection.set_progress_handler(lambda: int(self.cancelled()), 2000)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None

    def check_space(self):
        if shutil.disk_usage(self.directory.name).free < MIN_TEMP_FREE_BYTES:
            raise RuntimeError("Calculation stopped because the temporary drive has less than 1 GiB of free space.")

    def begin_file(self):
        self.connection.execute("SAVEPOINT current_file")

    def finish_file(self):
        self.connection.execute("RELEASE current_file")

    def discard_file(self):
        # Cancellation must not interrupt rollback/cleanup.
        self.connection.set_progress_handler(None, 0)
        self.connection.execute("ROLLBACK TO current_file")
        self.connection.execute("RELEASE current_file")
        self.connection.set_progress_handler(lambda: int(self.cancelled()), 2000)

    def append(self, frame, file_id, channels):
        self.check_space()
        tube, gas = frame["Tube"].iloc[0], frame["Gas"].iloc[0]
        values = np.column_stack([frame[f"channel{endpoint}:{c}"].to_numpy()
                                  for endpoint in (0, 1) for c in channels]).astype("<f8", copy=False)
        def rows():
            for (start, end, q0, q1, repeat), channel in zip(
                    frame[["start", "end", "q0", "q1", "repeat"]].itertuples(index=False, name=None), values):
                yield (tube, gas, start, file_id, end, next(self.sequence), q0, q1, channel.tobytes(), repeat)
        self.connection.executemany("INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows())

    def groups(self):
        return self.connection.execute("SELECT DISTINCT tube, gas FROM spans ORDER BY tube, gas").fetchall()

    def batches(self, tube, gas, size=CSV_CHUNK_ROWS):
        with closing(self.connection.execute(
                "SELECT start, end, file_id, q0, q1, channels, repeat FROM spans "
                "WHERE tube=? AND gas=? ORDER BY start, repeat, file_id, end, seq", (tube, gas))) as cursor:
            while rows := cursor.fetchmany(size):
                yield rows
