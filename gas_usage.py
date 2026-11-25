"""Integrate recorded gas flow, with one assumed second per repeated-time row."""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from functools import cached_property
import os
from pathlib import Path
import re
import sqlite3

import numpy as np
import pandas as pd
from gas_usage_storage import CSV_CHUNK_ROWS, DiskIntervalStore
from gas_usage_cache import (ComponentCache, component_cache_keys, load_result, query_key,
                             save_result, selection_fingerprint, source_fingerprint,
                             source_stamp)


# Exact geometric conversion; retain the standard conditions of the source sccm.
# NIST Handbook 44, Appendix C: 1 ft^3 = 28 316.846 592 cm^3.
CM3_PER_FT3 = 28316.846592
DEFAULT_N2 = ("Low N2(PV)", "High N2(PV)", "POCL N2(PV)", "Door Purge N2(PV)")
DEFAULT_O2 = ("High O2(PV)", "Low O2(PV)")
VOLUME_COLUMNS = ["N2 (ft³)", "O2 (ft³)", "N2+O2 (ft³)"]
HOUR_COLUMNS = ["N2 recorded (h)", "O2 recorded (h)"]
ISSUE_COLUMNS = ["File", "Issue", "Count"]
CHANNEL_COLUMNS = ["Month", "Tube", "Gas", "Channel", "Volume (ft³)", "Recorded (h)"]
NS_PER_SECOND = 1_000_000_000
NS_PER_HOUR = 3600 * NS_PER_SECOND
SHORT_GAP_NS = 3 * NS_PER_SECOND
FILENAME_EARLY_NS = NS_PER_SECOND  # HMI can record a row one second before its named shift.
EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


class CalculationCancelled(Exception):
    pass


@dataclass(frozen=True)
class GasUsageConfig:
    """Accept inclusive ISO dates, or YYYY-MM for whole-month callers."""
    start_month: str
    end_month: str
    n2_columns: tuple[str, ...] = DEFAULT_N2
    o2_columns: tuple[str, ...] = DEFAULT_O2
    time_column: str = "Time"
    max_gap_seconds: float | None = None
    filename_period: bool = True

    @cached_property
    def date_bounds(self):
        dates = []
        for value, is_end in ((self.start_month, False), (self.end_month, True)):
            if re.fullmatch(r"\d{4}-\d{2}", value):
                month = pd.Period(value, "M")
                day = month.end_time.normalize() if is_end else month.start_time
            else:
                day = pd.Timestamp(date.fromisoformat(value))
            dates.append(day)
        return dates[0], dates[1] + pd.Timedelta(days=1)

    @cached_property
    def months(self):
        start, stop = self.date_bounds
        return pd.period_range(start, stop - pd.Timedelta(days=1), freq="M")

    @cached_property
    def month_windows(self):
        start, stop = self.date_bounds
        return [(str(m), max(start.value, m.start_time.value),
                 min(stop.value, (m + 1).start_time.value)) for m in self.months]

    def validate(self):
        start, stop = self.date_bounds
        if start >= stop:
            raise ValueError("The start date must be on or before the end date.")
        if len(self.months) > 120:
            raise ValueError("Select up to 120 months per calculation.")
        if self.max_gap_seconds is not None and (not np.isfinite(self.max_gap_seconds) or self.max_gap_seconds <= 0):
            raise ValueError("The maximum recording gap must be greater than zero seconds.")
        if not self.time_column.strip():
            raise ValueError("Enter the time column name.")
        if not self.n2_columns or not self.o2_columns:
            raise ValueError("Specify at least one flow column for each gas (N2 and O2).")
        all_columns = self.n2_columns + self.o2_columns
        if len(set(all_columns)) != len(all_columns) or self.time_column in all_columns:
            raise ValueError("Each gas column must be unique and separate from the time column.")


@dataclass
class GasUsageResult:
    tubes: pd.DataFrame
    monthly: pd.DataFrame
    issues: pd.DataFrame
    files: pd.DataFrame
    settings: pd.DataFrame
    channels: pd.DataFrame


def normalize_column(value):
    return str(value).replace("\ufeff", "").replace("\xa0", " ").strip()


def tube_from_path(path):
    """Filename is authoritative, with a tube-directory fallback."""
    path = Path(path)
    for value in (path.stem.split("_")[0], path.parent.name):
        match = re.fullmatch(r"(?:TUBE|T|RT)[\s-]*0*(\d+)", value, re.IGNORECASE)
        if match:
            return f"TUBE{int(match[1]):02d}"
    return None


def proc_log_period(path):
    """TUBE##_YYMMDD_0/8/16, with a fixed eight-hour recording window."""
    match = re.fullmatch(r"TUBE(\d+)_(\d{6})_(\d{1,2})", Path(path).stem, re.IGNORECASE)
    if match is None or int(match[1]) < 1 or int(match[3]) not in (0, 8, 16):
        return None
    stamp = match[2]
    try:
        day = date(2000 + int(stamp[:2]), int(stamp[2:4]), int(stamp[4:])).toordinal()
    except ValueError:
        return None
    start = ((day - EPOCH_ORDINAL) * 24 + int(match[3])) * NS_PER_HOUR
    return f"TUBE{int(match[1]):02d}", (start, start + 8 * NS_PER_HOUR)


def read_flow_csv(path, config, encoding="utf-8-sig"):
    wanted = {config.time_column, *config.n2_columns, *config.o2_columns, "Tube ID", "TubeID"}
    return pd.read_csv(path, encoding=encoding, dtype=str, chunksize=CSV_CHUNK_ROWS,
                       usecols=lambda c: normalize_column(c) in wanted)


class IssueCounts:
    def __init__(self):
        self.counts = Counter()

    def append(self, issue):
        file, message, count = issue
        self.counts[file, message] += int(count)

    def extend(self, other):
        for row in other:
            self.append(row)

    def __iter__(self):
        return ([file, message, count] for (file, message), count in self.counts.items())


def parse_times(raw):
    # Numeric elapsed times are not calendar timestamps and cannot define months.
    text = raw.astype("string").str.strip()
    numeric = text.str.fullmatch(r"[+-]?\d+(?:\.\d+)?", na=False)
    compact = text.str.match(r"^\d{8}-\d{2}:\d{2}:\d{2}", na=False)
    text = text.mask(numeric)
    text.loc[compact] = text.loc[compact].str.replace(
        r"^(\d{4})(\d{2})(\d{2})-", r"\1-\2-\3 ", regex=True)
    result = pd.to_datetime(text, errors="coerce", format="mixed")
    if not pd.api.types.is_datetime64_dtype(result.dtype):
        raise ValueError("Use the equipment's local date and time consistently throughout the log.")
    return result


def read_time_bounds(path, time_column, cancelled=lambda: False, cooperate=lambda: None):
    """Inspect only timestamps; a filename alone cannot exclude a crossing run."""
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            first = last = None
            with pd.read_csv(path, encoding=encoding, chunksize=CSV_CHUNK_ROWS,
                             usecols=lambda c: normalize_column(c) == time_column) as reader:
                for chunk in reader:
                    cooperate()
                    if cancelled():
                        raise CalculationCancelled()
                    if len(chunk.columns) != 1:
                        return None
                    times = parse_times(chunk.iloc[:, 0]).dropna()
                    if not times.empty:
                        lo, hi = int(times.min().value), int(times.max().value)
                        first = lo if first is None else min(first, lo)
                        last = hi if last is None else max(last, hi)
            return None if first is None else (first, last)
        except UnicodeDecodeError:
            continue
    return None


class LogPeriodIndex:
    """Optional local cache of actual CSV time bounds, never of gas totals."""
    def __init__(self, cache_path=None):
        self.connection = None
        if cache_path is not None:
            try:
                Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
                self.connection = sqlite3.connect(str(cache_path), timeout=1)
                self.connection.execute("""CREATE TABLE IF NOT EXISTS periods_v1 (
                    path TEXT, time_column TEXT, size INTEGER, mtime_ns INTEGER,
                    ctime_ns INTEGER, first_ns INTEGER, last_ns INTEGER,
                    PRIMARY KEY(path, time_column))""")
            except (OSError, sqlite3.Error):
                self.close()

    def close(self):
        if self.connection is not None:
            try:
                self.connection.commit()
            except sqlite3.Error:
                pass
            self.connection.close()
            self.connection = None

    def bounds(self, path, time_column, cancelled, cooperate=lambda: None):
        stat = path.stat()
        fingerprint = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        key = (str(path.resolve()), time_column)
        if self.connection is not None:
            try:
                row = self.connection.execute(
                    "SELECT size, mtime_ns, ctime_ns, first_ns, last_ns FROM periods_v1 WHERE path=? AND time_column=?",
                    key).fetchone()
                if row is not None and row[:3] == fingerprint:
                    return row[3:]
            except sqlite3.Error:
                self.close()
        bounds = read_time_bounds(path, time_column, cancelled, cooperate)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != fingerprint:
            # A live log changed during inspection: include it and do not cache it.
            return None
        if bounds is not None and self.connection is not None:
            try:
                self.connection.execute("INSERT OR REPLACE INTO periods_v1 VALUES (?, ?, ?, ?, ?, ?, ?)",
                                        (*key, *fingerprint, *bounds))
            except sqlite3.Error:
                self.close()
        return bounds


def flow_intervals(df, path, tube, config, issues, *, carried=False, time_ns=None):
    """Integrate accepted adjacent rows and count repeated timestamps as samples."""
    file = str(path)

    def issue(message, count=1):
        if count:
            issues.append([file, message, int(count)])

    if config.time_column not in df:
        raise ValueError("The time column is missing.")
    fresh = slice(1, None) if carried else slice(None)
    ns = time_ns
    if ns is None:
        ns = parse_times(df[config.time_column]).to_numpy(dtype="datetime64[ns]").view(np.int64)
    valid_time = ns != np.iinfo(np.int64).min
    issue("Invalid or missing timestamp (adjacent intervals excluded)", (~valid_time[fresh]).sum())
    # Compute differences only for valid timestamps, avoiding NaT overflow.
    pair = valid_time[:-1] & valid_time[1:]
    seconds = np.zeros(len(df) - 1)
    seconds[pair] = (ns[1:][pair] - ns[:-1][pair]) / 1e9
    repeated = pair & (seconds == 0)
    issue("Repeated timestamp: each additional row contributes one second at its recorded flow rate", repeated.sum())
    # Occurrence numbers survive chunk boundaries and distinguish repeated rows
    # from duplicate copies of the same file in the disk overlap path.
    if "_gas_repeat" in df:
        ordinal = df["_gas_repeat"].to_numpy(dtype=np.int64)
    else:
        positions = np.arange(len(df))
        ordinal = positions - np.maximum.accumulate(np.where(np.r_[True, ~repeated], positions, 0))
    valid = pair & (seconds > 0)
    if config.max_gap_seconds is not None:
        issue(f"Recording gap exceeds {config.max_gap_seconds:g} seconds (interval excluded)", (pair & (seconds > config.max_gap_seconds)).sum())
        valid &= seconds <= config.max_gap_seconds
    start, stop = config.date_bounds
    valid &= (ns[:-1] < stop.value) & (ns[1:] > start.value)
    frames = []
    for gas, columns in (("N2", config.n2_columns), ("O2", config.o2_columns)):
        missing = [c for c in columns if c not in df]
        if missing:
            if not carried:
                issue(f"{gas} excluded because columns are missing ({', '.join(missing)})")
            continue
        values = df[list(columns)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        rates = values.sum(axis=1)
        good = np.isfinite(values).all(axis=1) & (values >= 0).all(axis=1) & np.isfinite(rates)
        issue(f"{gas} negative, missing, or nonnumeric flow (adjacent intervals excluded)", (~good)[fresh].sum())
        mask = valid & good[:-1] & good[1:]
        if mask.any():
            intervals = pd.DataFrame({"Tube": tube, "Gas": gas,
                "start": ns[:-1][mask], "end": ns[1:][mask],
                "q0": rates[:-1][mask], "q1": rates[1:][mask], "File": file, "repeat": 0})
            for index, column in enumerate(columns):
                intervals[f"channel0:{column}"] = values[:-1, index][mask]
                intervals[f"channel1:{column}"] = values[1:, index][mask]
            frames.append(intervals)
        # These are extra assumed seconds, not synthetic wall-clock spans:
        # overlap removal must not erase them against the next normal interval.
        mask = repeated & good[1:] & (ns[1:] >= start.value) & (ns[1:] < stop.value)
        if mask.any():
            samples = pd.DataFrame({"Tube": tube, "Gas": gas,
                "start": ns[1:][mask], "end": ns[1:][mask],
                "q0": rates[1:][mask], "q1": rates[1:][mask], "File": file,
                "repeat": ordinal[1:][mask]})
            for index, column in enumerate(columns):
                samples[f"channel0:{column}"] = values[1:, index][mask]
                samples[f"channel1:{column}"] = values[1:, index][mask]
            frames.append(samples)
    valid_ns = ns[valid_time]
    first, last = (pd.Timestamp(valid_ns.min()), pd.Timestamp(valid_ns.max())) if valid_ns.size else (pd.NaT, pd.NaT)
    record = [file, tube, len(df), str(first), str(last)]
    return frames, record


def stage_flow_file(store, path, file_id, tube, config, cancelled, cooperate, expected_bounds=None):
    """Stage one CSV atomically; retain one boundary row between read chunks."""
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        store.begin_file()
        issues = IssueCounts()
        count, carry, carry_ns, first, last = 0, None, None, pd.NaT, pd.NaT
        last_valid = np.iinfo(np.int64).min
        last_repeat = 0
        tube_ids = {}
        try:
            with read_flow_csv(path, config, encoding) as reader:
                for chunk in reader:
                    cooperate()
                    if cancelled():
                        raise CalculationCancelled()
                    chunk.columns = [normalize_column(c) for c in chunk.columns]
                    if chunk.columns.duplicated().any():
                        raise ValueError("Column names are duplicated after trimming whitespace.")
                    count += len(chunk)
                    if config.time_column not in chunk:
                        raise ValueError("The time column is missing.")
                    times = parse_times(chunk[config.time_column])
                    if expected_bounds is not None:
                        outside = times.notna() & ((times < pd.Timestamp(expected_bounds[0])) |
                                                  (times > pd.Timestamp(expected_bounds[1])))
                        if outside.any():
                            issues.append([str(path), "Row outside the named 8-hour window (up to one second early is allowed)", int(outside.sum())])
                        chunk = chunk.loc[~outside].copy()
                        times = times.loc[~outside]
                    ns = times.to_numpy(dtype="datetime64[ns]").astype("int64")
                    if not len(ns):
                        continue
                    high = np.maximum.accumulate(np.maximum(ns, last_valid))
                    prior = np.r_[last_valid, high[:-1]]
                    reverse = times.notna().to_numpy() & (ns < prior)
                    if reverse.any():
                        issues.append([str(path), "Backward timestamp excluded (earlier than the last valid timestamp)", int(reverse.sum())])
                    chunk = chunk.loc[~reverse].copy()
                    if chunk.empty:
                        continue
                    accepted_ns = ns[~reverse]
                    valid_indices = np.flatnonzero(accepted_ns != np.iinfo(np.int64).min)
                    ordinals = np.zeros(len(chunk), dtype=np.int64)
                    if len(valid_indices):
                        valid_ns = accepted_ns[valid_indices]
                        positions = np.arange(len(valid_ns))
                        starts = np.r_[True, valid_ns[1:] != valid_ns[:-1]]
                        group_start = np.maximum.accumulate(np.where(starts, positions, 0))
                        ranks = positions - group_start
                        if valid_ns[0] == last_valid:
                            ranks[group_start == 0] += last_repeat + 1
                        ordinals[valid_indices] = ranks
                        last_repeat = int(ranks[-1])
                        last_valid = int(valid_ns[-1])
                    chunk["_gas_repeat"] = ordinals
                    for column in ("Tube ID", "TubeID"):
                        if column in chunk:
                            ids = pd.to_numeric(chunk[column], errors="coerce").dropna()
                            tube_ids[column] = tube_ids.get(column, False) or not ids.empty
                            if (ids != int(tube[4:])).any():
                                raise ValueError("Tube ID differs from the tube in the file or folder name.")
                    block = pd.concat([carry, chunk], ignore_index=True) if carry is not None else chunk
                    block_ns = np.r_[carry_ns, accepted_ns] if carry is not None else accepted_ns
                    pieces, record = flow_intervals(block, path, tube, config, issues,
                                                    carried=carry is not None, time_ns=block_ns)
                    for frame in pieces:
                        columns = config.n2_columns if frame["Gas"].iloc[0] == "N2" else config.o2_columns
                        store.append(frame, file_id, columns)
                    lo, hi = pd.Timestamp(record[3]), pd.Timestamp(record[4])
                    if pd.notna(lo):
                        first = lo if pd.isna(first) else min(first, lo)
                        last = hi if pd.isna(last) else max(last, hi)
                    carry = block.iloc[-1:].copy()  # Keep the repeat ordinal, not the entire chunk.
                    carry_ns = int(block_ns[-1])
            if count < 2:
                raise ValueError("The time column is missing or the log has fewer than two rows.")
            if not all(tube_ids.values()):
                raise ValueError("Tube ID differs from the tube in the file or folder name.")
            store.finish_file()
            return [str(path), tube, count, str(first), str(last)], issues
        except UnicodeDecodeError:
            store.discard_file()
            continue
        except BaseException:
            store.discard_file()
            raise
    raise ValueError("Could not decode the CSV file.")


class NeedsDiskOrdering(Exception):
    pass


class SourceChanged(Exception):
    pass


class IntervalTotals:
    def __init__(self, config):
        self.config = config
        self.values = {}

    def add_repeated(self, tube, gas, timestamps, rates, channels):
        """One second at each repeated row's own rate, assigned to its timestamp month."""
        for month, lo, hi in self.config.month_windows:
            keep = (timestamps >= lo) & (timestamps < hi)
            if keep.any():
                values = np.r_[rates[keep].sum() / 60 / CM3_PER_FT3,
                               keep.sum() / 3600,
                               channels[keep].sum(axis=0) / 60 / CM3_PER_FT3]
                self.values.setdefault((tube, gas, month), np.zeros(len(values)))[:] += values

    def add(self, tube, gas, start, end, q0, q1, channels, unique_start=None):
        n = channels.shape[1] // 2
        if unique_start is None:
            unique_start = start
        for month, month_start, month_stop in self.config.month_windows:
            lo = np.maximum(unique_start, month_start)
            hi = np.minimum(end, month_stop)
            keep = hi > lo
            if not keep.any():
                continue
            duration = end[keep] - start[keep]
            left_fraction = (lo[keep] - start[keep]) / duration
            right_fraction = (hi[keep] - start[keep]) / duration
            included = hi[keep] - lo[keep]
            seconds = included / NS_PER_SECOND
            short = (duration < SHORT_GAP_NS) & (duration != NS_PER_SECOND)
            # Keep actual timestamps for ordering and clipping; only the duration
            # weight changes. Clipped intervals retain their share of the one second.
            seconds[short] = included[short] / duration[short]
            left = q0[keep] + (q1[keep] - q0[keep]) * left_fraction
            right = q0[keep] + (q1[keep] - q0[keep]) * right_fraction
            volume = np.sum((left + right) * .5 * seconds / 60) / CM3_PER_FT3
            c0, c1 = channels[keep, :n], channels[keep, n:]
            left_c = c0 + (c1 - c0) * left_fraction[:, None]
            right_c = c0 + (c1 - c0) * right_fraction[:, None]
            channel_volume = np.sum((left_c + right_c) * .5 * seconds[:, None] / 60, axis=0) / CM3_PER_FT3
            values = np.r_[volume, seconds.sum() / 3600, channel_volume]
            self.values.setdefault((tube, gas, month), np.zeros(n + 2))[:] += values

    def merge(self, other):
        for key, values in other.values.items():
            self.values.setdefault(key, np.zeros_like(values))[:] += values

    def frames(self):
        rows, channel_rows = [], []
        for (tube, gas, month), values in sorted(self.values.items()):
            rows.append([month, tube, gas, values[0], values[1]])
            columns = self.config.n2_columns if gas == "N2" else self.config.o2_columns
            for index, column in enumerate(columns):
                channel_rows.append([month, tube, gas, column, values[index + 2], values[1]])
        return (pd.DataFrame(rows, columns=["Month", "Tube", "Gas", "Volume", "Hours"]),
                pd.DataFrame(channel_rows, columns=CHANNEL_COLUMNS))


class DirectIntervalStore:
    """Constant-space fast path for one chronologically ordered, isolated file."""
    def __init__(self, config):
        self.config = config
        self.begin_file()

    def begin_file(self):
        self.totals = IntervalTotals(self.config)
        self.previous_end = {}

    def finish_file(self):
        pass

    def discard_file(self):
        self.begin_file()

    def append(self, frame, file_id, columns):
        tube, gas = frame["Tube"].iloc[0], frame["Gas"].iloc[0]
        start, end = frame["start"].to_numpy(), frame["end"].to_numpy()
        if (frame["repeat"] > 0).all():
            channels = np.column_stack([frame[f"channel1:{c}"].to_numpy() for c in columns])
            self.totals.add_repeated(tube, gas, start, frame["q1"].to_numpy(), channels)
            return
        if start[0] < self.previous_end.get(gas, np.iinfo(np.int64).min) or (start[1:] < end[:-1]).any():
            raise NeedsDiskOrdering()
        self.previous_end[gas] = int(end[-1])
        channels = np.column_stack([frame[f"channel{endpoint}:{c}"].to_numpy()
                                   for endpoint in (0, 1) for c in columns])
        self.totals.add(tube, gas, start, end, frame["q0"].to_numpy(), frame["q1"].to_numpy(), channels)


def overlap_components(paths, bounds):
    """Only files with potentially overlapping actual time ranges need disk ordering."""
    tubes = {}
    for index, path in enumerate(paths):
        tubes.setdefault(tube_from_path(path), []).append(index)
    components = []
    for indices in tubes.values():
        if any(bounds[i] is None for i in indices):
            components.append(indices)
            continue
        group, covered = [], None
        for index in sorted(indices, key=lambda i: (bounds[i][0], i)):
            start, end = bounds[index]
            if covered is not None and start >= covered:
                components.append(group)
                group = []
            group.append(index)
            # Equal-time-only files still contain one-second assumed samples.
            extent = max(end, start + 1)
            covered = extent if len(group) == 1 else max(covered, extent)
        if group:
            components.append(group)
    return components


def integrate_intervals(store, config, issues, paths, cancelled=lambda: False, cooperate=lambda: None):
    """Read ordered disk batches, carrying covered-end state across boundaries."""
    totals = IntervalTotals(config)
    for tube, gas in store.groups():
        columns = config.n2_columns if gas == "N2" else config.o2_columns
        n = len(columns)
        previous_end = np.iinfo(np.int64).min
        previous_repeat = None
        for batch in store.batches(tube, gas, CSV_CHUNK_ROWS):
            cooperate()
            if cancelled():
                raise CalculationCancelled()
            start = np.array([r[0] for r in batch], dtype=np.int64)
            end = np.array([r[1] for r in batch], dtype=np.int64)
            files = np.array([r[2] for r in batch], dtype=np.int64)
            q0 = np.array([r[3] for r in batch])
            q1 = np.array([r[4] for r in batch])
            channels = np.frombuffer(b"".join(r[5] for r in batch), dtype="<f8").reshape(-1, 2 * n)
            ordinal = np.array([r[6] for r in batch], dtype=np.int64)
            repeated = ordinal > 0
            if repeated.any():
                indices = np.flatnonzero(repeated)
                keep = np.ones(len(indices), dtype=bool)
                for j, i in enumerate(indices):
                    key = (int(start[i]), int(ordinal[i]))
                    if key == previous_repeat:
                        keep[j] = False
                        issues.append([str(paths[files[i]]), f"{gas} identical repeated-timestamp rows across files counted once", 1])
                    previous_repeat = key
                selected = indices[keep]
                totals.add_repeated(tube, gas, start[selected], q1[selected], channels[selected, n:])
            normal = ~repeated
            if not normal.any():
                continue
            start, end, files, q0, q1, channels = (a[normal] for a in (start, end, files, q0, q1, channels))
            covered = np.maximum.accumulate(end)
            prior_end = np.concatenate(([previous_end], covered[:-1]))
            prior_end = np.maximum(prior_end, previous_end)
            unique_start = np.maximum(start, prior_end)
            previous_end = max(previous_end, int(covered[-1]))
            ids, counts = np.unique(files[unique_start > start], return_counts=True)
            for file_id, count in zip(ids, counts):
                issues.append([str(paths[file_id]), f"{gas} duplicate or overlapping intervals counted once (earliest start, then file path)", count])
            totals.add(tube, gas, start, end, q0, q1, channels, unique_start)
    return totals


def summarize_usage(integrated, identities, config):
    months = config.months.astype(str)
    index = pd.MultiIndex.from_tuples(
        [(month, tube) for month in months for tube in sorted(identities)],
        names=["Month", "Tube"])
    tubes = pd.DataFrame(index=index)
    for gas in ("N2", "O2"):
        subset = integrated[integrated["Gas"] == gas].set_index(["Month", "Tube"])
        tubes[f"{gas} (ft³)"] = subset["Volume"]
        tubes[f"{gas} recorded (h)"] = subset["Hours"]
    tubes["N2+O2 (ft³)"] = tubes["N2 (ft³)"] + tubes["O2 (ft³)"]
    tubes["Status"] = np.where(tubes[VOLUME_COLUMNS[:2]].isna().all(axis=1), "No data",
                              np.where(tubes[VOLUME_COLUMNS[:2]].isna().any(axis=1), "Partial coverage", "Recorded intervals"))
    tubes = tubes.reset_index()
    monthly = tubes.groupby(["Month"], as_index=False)[VOLUME_COLUMNS[:2] + HOUR_COLUMNS].sum(min_count=1)
    monthly["N2+O2 (ft³)"] = monthly["N2 (ft³)"] + monthly["O2 (ft³)"]
    statuses = tubes.groupby("Month")["Status"].agg(
        lambda values: "No data" if (values == "No data").all() else
        ("Partial coverage" if (values != "Recorded intervals").any() else "Recorded intervals"))
    monthly["Status"] = [statuses.loc[m] for m in monthly["Month"]]
    return (tubes[["Month", "Tube", *VOLUME_COLUMNS, *HOUR_COLUMNS, "Status"]],
            monthly[["Month", *VOLUME_COLUMNS, *HOUR_COLUMNS, "Status"]])


def discover_csv(root, cancelled):
    """Use directory entry types, rather than a Path.is_file stat for every entry."""
    pending = [root]
    paths = []
    while pending:
        if cancelled():
            raise CalculationCancelled()
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if cancelled():
                        raise CalculationCancelled()
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        elif entry.name.lower().endswith(".csv") and len(entry.name) > 4 and entry.is_file():
                            paths.append(Path(entry.path))
                    except OSError:
                        continue  # A directory entry can disappear during enumeration.
        except OSError:
            continue  # Match pathlib glob's unreadable subtree handling.
    return sorted(paths)


def calculate_monthly_usage(root, config, progress=lambda done, total, name: None,
                            cancelled=lambda: False, *, period_cache=None, cooperate=lambda: None, temp_parent=None):
    config.validate()
    root = Path(root)
    if not root.is_dir():
        raise ValueError("The process log folder does not exist.")
    paths = discover_csv(root, cancelled)
    if cancelled():
        raise CalculationCancelled()
    issues, records, identities = IssueCounts(), [], set()
    candidates, candidate_bounds, rejected_names = [], [], []
    start, stop = config.date_bounds
    period_start, period_end = start.value, stop.value
    prior_boundary_files = next_boundary_files = 0
    if config.filename_period:
        for i, path in enumerate(paths):
            if cancelled():
                raise CalculationCancelled()
            if i % 100 == 0:
                progress(i, len(paths), "Selecting dates by filename without reading CSV contents")
            parsed = proc_log_period(path)
            if parsed is None:
                rejected_names.append(str(path))
                issues.append([str(path), "Filename excluded: expected TUBE##_YYMMDD_0/8/16.csv", 1])
                continue
            tube, bounds = parsed
            identities.add(tube)
            # A prior 16:00 file may end in same-time samples at midnight;
            # the next 00:00 file may have a slightly early first row.
            prior_boundary = bounds[1] == period_start
            next_boundary = bounds[0] == period_end
            if prior_boundary or next_boundary or period_start <= bounds[0] < period_end:
                candidates.append(path)
                candidate_bounds.append(bounds)
                prior_boundary_files += prior_boundary
                next_boundary_files += next_boundary

    initial_stamps = None
    if config.filename_period and period_cache is not None:
        initial_stamps = {}
        for path in candidates:
            if cancelled():
                raise CalculationCancelled()
            initial_stamps[path] = source_stamp(path)

    def current_fingerprint(stamps=None):
        if period_cache is None:
            return None
        if config.filename_period:
            return selection_fingerprint(candidates, identities, rejected_names, stamps=stamps)
        return source_fingerprint(paths)

    key = query_key(root, config)
    fingerprint = current_fingerprint(initial_stamps)
    cached = load_result(period_cache, key, fingerprint)
    if cached is not None:
        if cancelled():
            raise CalculationCancelled()
        result = GasUsageResult(**cached)
        result.settings.loc[result.settings["Setting"] == "Result cache", "Value"] = "Reused (source metadata unchanged)"
        if config.filename_period:
            for setting, value in (("Scanned files", len(paths)),
                                   ("Skipped outside period", len(paths) - len(candidates) - len(rejected_names)),
                                   ("Rejected filename format", len(rejected_names)),
                                   ("Prior boundary files", prior_boundary_files),
                                   ("Next boundary files", next_boundary_files)):
                result.settings.loc[result.settings["Setting"] == setting, "Value"] = value
        progress(len(paths), len(paths), "Reusing results for unchanged logs")
        return result
    if not config.filename_period:
        index = LogPeriodIndex(period_cache)
        try:
            for i, path in enumerate(paths):
                if cancelled():
                    raise CalculationCancelled()
                progress(i, len(paths), "Checking recording dates using the time column (legacy format)")
                try:
                    bounds = index.bounds(path, config.time_column, cancelled, cooperate)
                except (ValueError, OSError, pd.errors.ParserError):
                    bounds = None
                if bounds is not None and (bounds[1] < period_start or bounds[0] >= period_end):
                    tube = tube_from_path(path)
                    if tube is not None:
                        identities.add(tube)
                    continue
                candidates.append(path)
                candidate_bounds.append(bounds)
        finally:
            index.close()
    totals = IntervalTotals(config)
    completed = 0
    direct_files, disk_files, reused_files = 0, 0, 0
    def process_file(store, i, component_records, component_issues):
        path = candidates[i]
        tube = tube_from_path(path)
        if tube is None:
            component_issues.append([str(path), "File excluded because the tube could not be identified from the file or folder name", 1])
            return
        try:
            known = candidate_bounds[i]
            expected = (known[0] - FILENAME_EARLY_NS, known[1]) if config.filename_period else None
            record, file_issues = stage_flow_file(
                store, path, i, tube, config, cancelled, cooperate, expected_bounds=expected)
            if not config.filename_period and known is not None:
                lo, hi = pd.Timestamp(record[3]), pd.Timestamp(record[4])
                if pd.notna(lo) and (lo.value < known[0] or hi.value > known[1]):
                    raise SourceChanged("The log's recording period changed during calculation. Run the calculation again.")
            component_issues.extend(file_issues)
            component_records.append(record)
        except (ValueError, OSError, pd.errors.ParserError) as exc:
            component_issues.append([str(path), f"File excluded: {exc}", 1])

    cache = ComponentCache(period_cache)

    def calculate_component(component):
        nonlocal completed
        if cancelled():
            raise CalculationCancelled()
        component_paths = [candidates[i] for i in component]
        ck = cf = None
        if period_cache is not None:
            stamps = ([source_stamp(p) for p in component_paths] if initial_stamps is None else
                      [initial_stamps[p] for p in component_paths])
            ck, cf = component_cache_keys(key, stamps)
        cached = cache.load(ck, cf)
        if cached is not None and initial_stamps is not None:
            # After reading earlier files, do not reuse a cached result for a now-live log.
            live_key, live_fingerprint = component_cache_keys(
                key, [source_stamp(p) for p in component_paths])
            if live_fingerprint != cf:
                ck, cf = live_key, live_fingerprint
                cached = None
        if cached is not None:
            partial = IntervalTotals(config)
            partial.values = {(tube, gas, month): np.asarray(values, dtype=float)
                              for tube, gas, month, values in cached["totals"]}
            completed += len(component)
            progress(completed, len(candidates), "Reusing subtotals for unchanged files")
            return partial, cached["records"], cached["issues"], "reused"

        component_issues = IssueCounts()
        component_records = []
        partial = None
        mode = "direct"
        if len(component) == 1:
            i = component[0]
            progress(completed, len(candidates), f"Integrating flow · {candidates[i].name}")
            direct = DirectIntervalStore(config)
            try:
                process_file(direct, i, component_records, component_issues)
                partial = direct.totals
                completed += 1
            except NeedsDiskOrdering:
                pass  # File staging is atomic; no partial totals or records survive.
        if partial is None:
            mode = "disk"
            with DiskIntervalStore(cancelled, temp_parent=temp_parent) as store:
                for i in component:
                    progress(completed, len(candidates), f"Integrating flow · {candidates[i].name}")
                    process_file(store, i, component_records, component_issues)
                    completed += 1
                progress(completed, len(candidates), "Resolving overlaps and calculating monthly totals")
                partial = integrate_intervals(store, config, component_issues, candidates, cancelled, cooperate)
        issue_rows = list(component_issues)
        if cf is not None and source_fingerprint(component_paths) == cf:
            cache.queue(ck, cf, {
                "totals": [[*group_key, values.tolist()] for group_key, values in partial.values.items()],
                "records": component_records, "issues": issue_rows})
        return partial, component_records, issue_rows, mode

    def merge_component(component, outcome):
        nonlocal direct_files, disk_files, reused_files
        partial, component_records, issue_rows, mode = outcome
        totals.merge(partial)
        records.extend(component_records)
        identities.update(r[1] for r in component_records)
        issues.extend(issue_rows)
        if mode == "direct":
            direct_files += len(component)
        elif mode == "disk":
            disk_files += len(component)
        else:
            reused_files += len(component)

    try:
        # Keep canonical shifts separate even if HMI timestamps touch or show
        # the one-second boundary skew. No synthetic interval bridges files.
        # Multiple copies claiming the same window still use disk deduplication.
        for component in overlap_components(candidates, candidate_bounds):
            merge_component(component, calculate_component(component))
        progress(len(candidates), len(candidates), "Calculating monthly totals")
        if cancelled():
            raise CalculationCancelled()
        integrated, channels = totals.frames()
    except sqlite3.OperationalError:
        if cancelled():
            raise CalculationCancelled() from None
        raise
    finally:
        cache.close()
    tubes, monthly = summarize_usage(integrated, identities, config)
    settings = pd.DataFrame([
        ["Root", str(root.resolve())], ["Months", f"{config.months[0]} ~ {config.months[-1]}"],
        ["Date range", f"{start.date()} ~ {(stop - pd.Timedelta(days=1)).date()}"],
        ["File selection", "Filename TUBE##_YYMMDD_0/8/16; selected dates plus adjacent midnight boundary shifts; clip by actual time"
         if config.filename_period else "Legacy mode: actual timestamp bounds intersect query"],
        ["Scanned files", len(paths)], ["Candidate files", len(candidates)],
        ["Prior boundary files", prior_boundary_files],
        ["Next boundary files", next_boundary_files],
        ["Skipped outside period", len(paths) - len(candidates) - len(rejected_names)],
        ["Rejected filename format", len(rejected_names)],
        ["Processing", f"Streaming {CSV_CHUNK_ROWS} rows per batch; isolated ordered files integrate directly; disk ordering only for overlaps"],
        ["Direct streaming files", direct_files], ["Disk ordering files", disk_files],
        ["Reused files", reused_files],
        ["Result cache", "Fresh calculation"],
        ["Time", config.time_column], ["N2 sccm columns", ", ".join(config.n2_columns)],
        ["O2 sccm columns", ", ".join(config.o2_columns)],
        ["Maximum gap (s)", "Unlimited" if config.max_gap_seconds is None else config.max_gap_seconds],
        ["Formula", "(sum((q0+q1)/2 * effective_seconds/60) + sum(repeated_row_rate/60)) / 28316.846592"],
        ["Short timestamp gaps", "0 ≤ elapsed < 3 s counts as 1 s; 3 s or more uses elapsed time. Actual timestamps govern calendar clipping."],
        ["Repeated timestamps", "Each additional same-time row contributes one second at its own rate, in its timestamp month"],
        ["Gap limit precedence", "The optional maximum gap applies to positive raw elapsed time before short-gap weighting; same-time samples remain included."],
        ["Excluded rows", "Remove backwards rows; filename mode permits up to 1 second early, otherwise excludes rows outside the named 8-hour window"],
        ["Volume basis", "ft³ at the same standard conditions as the source sccm; no T/P correction"],
        ["Scope", "Recorded process intervals only; excludes unlogged idle/purge usage and final-sample extrapolation"],
        ["Overlap", "Canonical shifts are independent. Legacy/same-window overlaps count once per Tube/gas, earliest start then file path; uncovered tails retained."],
        ["Missing", "Blank is unavailable, not zero. Monthly totals sum available Tube values."],
        ["Recorded hours", "Monthly hours are summed Tube-hours, not elapsed calendar hours"],
    ], columns=["Setting", "Value"])
    result = GasUsageResult(tubes, monthly, pd.DataFrame(list(issues), columns=ISSUE_COLUMNS),
                          pd.DataFrame(records, columns=["File", "Tube", "Rows", "First", "Last"]),
                          settings, channels)
    if fingerprint is not None and current_fingerprint() == fingerprint:
        save_result(period_cache, key, fingerprint, result)
    return result


def select_usage_month(result, month):
    """Slice volumes for export; keep original file diagnostics and their scan scope."""
    settings = result.settings.copy()
    settings.loc[settings["Setting"] == "Months", "Value"] = f"{month} ~ {month}"
    dates = settings["Setting"] == "Date range"
    start, end = settings.loc[dates, "Value"].iloc[0].split(" ~ ")
    period = pd.Period(month, "M")
    first = max(start, str(period.start_time.date()))
    last = min(end, str(period.end_time.date()))
    settings.loc[dates, "Value"] = f"{first} ~ {last}"
    settings.loc[len(settings)] = ["Review scan scope", f"{start} ~ {end}"]
    return GasUsageResult(
        result.tubes[result.tubes["Month"] == month].copy(),
        result.monthly[result.monthly["Month"] == month].copy(),
        result.issues.copy(), result.files.copy(), settings,
        result.channels[result.channels["Month"] == month].copy())


def export_gas_usage(result, path):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in (("Monthly totals", result.monthly), ("Tube monthly", result.tubes),
                            ("Channel monthly", result.channels),
                            ("Review", result.issues), ("Files", result.files), ("Settings", result.settings)):
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for column in sheet.columns:
                letter = column[0].column_letter
                sheet.column_dimensions[letter].width = min(64, max(15, len(str(column[0].value)) + 3))
                for cell in column[1:]:
                    if isinstance(cell.value, float):
                        cell.number_format = "0.000000"
                    elif isinstance(cell.value, str):
                        cell.data_type = "s"
