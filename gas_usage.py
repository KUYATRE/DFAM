"""Integrate recorded gas flow without extending it beyond observed intervals."""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

import numpy as np
import pandas as pd
from gas_usage_storage import CSV_CHUNK_ROWS, DiskIntervalStore
from gas_usage_cache import (query_key, source_fingerprint, load_result, save_result,
                             component_key, load_component, save_component, selection_fingerprint)


# Exact geometric conversion; retain the standard conditions of the source sccm.
# NIST Handbook 44, Appendix C: 1 ft^3 = 28 316.846 592 cm^3.
CM3_PER_FT3 = 28316.846592
DEFAULT_N2 = ("Low N2(PV)", "High N2(PV)", "POCL N2(PV)", "Door Purge N2(PV)")
DEFAULT_O2 = ("High O2(PV)", "Low O2(PV)")
VOLUME_COLUMNS = ["N2 (ft³)", "O2 (ft³)", "N2+O2 (ft³)"]
HOUR_COLUMNS = ["N2 recorded (h)", "O2 recorded (h)"]
ISSUE_COLUMNS = ["File", "Issue", "Count"]
CHANNEL_COLUMNS = ["Month", "Tube", "Gas", "Channel", "Volume (ft³)", "Recorded (h)"]


class CalculationCancelled(Exception):
    pass


@dataclass(frozen=True)
class GasUsageConfig:
    start_month: str
    end_month: str
    n2_columns: tuple[str, ...] = DEFAULT_N2
    o2_columns: tuple[str, ...] = DEFAULT_O2
    time_column: str = "Time"
    max_gap_seconds: float | None = None
    filename_period: bool = True

    def validate(self):
        start, end = pd.Period(self.start_month, "M"), pd.Period(self.end_month, "M")
        if start > end:
            raise ValueError("시작 월은 종료 월보다 늦을 수 없습니다.")
        if end.ordinal - start.ordinal > 119:
            raise ValueError("한 번에 최대 120개월까지 집계할 수 있습니다.")
        if self.max_gap_seconds is not None and (not np.isfinite(self.max_gap_seconds) or self.max_gap_seconds <= 0):
            raise ValueError("허용 기록 간격은 0보다 큰 초 단위 값이어야 합니다.")
        if not self.time_column.strip():
            raise ValueError("시간 컬럼을 지정하세요.")
        if not self.n2_columns or not self.o2_columns:
            raise ValueError("N2와 O2 각각 하나 이상의 유량 컬럼을 지정하세요.")
        all_columns = self.n2_columns + self.o2_columns
        if len(set(all_columns)) != len(all_columns) or self.time_column in all_columns:
            raise ValueError("가스 컬럼을 중복 지정하거나 시간 컬럼과 겹치게 지정할 수 없습니다.")


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
    date = match[2]
    try:
        start = pd.Timestamp(year=2000 + int(date[:2]), month=int(date[2:4]),
                             day=int(date[4:]), hour=int(match[3]))
    except ValueError:
        return None
    return f"TUBE{int(match[1]):02d}", (start.value, (start + pd.Timedelta(hours=8)).value)


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
        raise ValueError("시간은 설비의 현지 날짜/시각이어야 합니다 (시간대 혼합 불가).")
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


def flow_intervals(df, path, tube, config, issues, *, carried=False):
    """Keep each original adjacent pair; never bridge invalid rows or files."""
    file = str(path)

    def issue(message, count=1):
        if count:
            issues.append([file, message, int(count)])

    if config.time_column not in df:
        raise ValueError("시간 컬럼이 없습니다.")
    fresh = slice(1, None) if carried else slice(None)
    times = parse_times(df[config.time_column])
    issue("잘못되거나 누락된 시각 (인접 구간 제외)", times.isna().iloc[fresh].sum())
    ns = times.to_numpy(dtype="datetime64[ns]").astype("int64")
    valid_time = times.notna().to_numpy()
    # Compute differences only for valid timestamps, avoiding NaT overflow.
    pair = valid_time[:-1] & valid_time[1:]
    seconds = np.zeros(len(df) - 1)
    seconds[pair] = (ns[1:][pair] - ns[:-1][pair]) / 1e9
    issue("중복 또는 역행 시각 구간 제외", (pair & (seconds <= 0)).sum())
    valid = pair & (seconds > 0)
    if config.max_gap_seconds is not None:
        issue(f"{config.max_gap_seconds:g}초 초과 기록 간격 제외", (pair & (seconds > config.max_gap_seconds)).sum())
        valid &= seconds <= config.max_gap_seconds
    bounds = (pd.Period(config.start_month, "M").start_time.value,
              (pd.Period(config.end_month, "M") + 1).start_time.value)
    valid &= (ns[:-1] < bounds[1]) & (ns[1:] > bounds[0])
    frames = []
    for gas, columns in (("N2", config.n2_columns), ("O2", config.o2_columns)):
        missing = [c for c in columns if c not in df]
        if missing:
            if not carried:
                issue(f"{gas} 집계 제외: 컬럼 누락 ({', '.join(missing)})")
            continue
        values = df[list(columns)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        rates = values.sum(axis=1)
        good = np.isfinite(values).all(axis=1) & (values >= 0).all(axis=1) & np.isfinite(rates)
        issue(f"{gas} 음수/결측/비수치 유량 (인접 구간 제외)", (~good)[fresh].sum())
        mask = valid & good[:-1] & good[1:]
        if mask.any():
            intervals = pd.DataFrame({"Tube": tube, "Gas": gas,
                "start": ns[:-1][mask], "end": ns[1:][mask],
                "q0": rates[:-1][mask], "q1": rates[1:][mask], "File": file})
            for index, column in enumerate(columns):
                intervals[f"channel0:{column}"] = values[:-1, index][mask]
                intervals[f"channel1:{column}"] = values[1:, index][mask]
            frames.append(intervals)
    first, last = times.min(), times.max()
    record = [file, tube, len(df), str(first), str(last)]
    return frames, record


def stage_flow_file(store, path, file_id, tube, config, cancelled, cooperate, expected_bounds=None):
    """Stage one CSV atomically; retain one boundary row between read chunks."""
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        store.begin_file()
        issues = IssueCounts()
        count, carry, first, last = 0, None, pd.NaT, pd.NaT
        tube_ids = {}
        try:
            with read_flow_csv(path, config, encoding) as reader:
                for chunk in reader:
                    cooperate()
                    if cancelled():
                        raise CalculationCancelled()
                    chunk.columns = [normalize_column(c) for c in chunk.columns]
                    if chunk.columns.duplicated().any():
                        raise ValueError("공백 제거 후 중복되는 컬럼이 있습니다.")
                    for column in ("Tube ID", "TubeID"):
                        if column in chunk:
                            ids = pd.to_numeric(chunk[column], errors="coerce").dropna()
                            tube_ids[column] = tube_ids.get(column, False) or not ids.empty
                            if (ids != int(tube[4:])).any():
                                raise ValueError("Tube ID와 파일명/폴더의 Tube가 일치하지 않습니다.")
                    count += len(chunk)
                    block = pd.concat([carry, chunk], ignore_index=True) if carry is not None else chunk
                    pieces, record = flow_intervals(block, path, tube, config, issues, carried=carry is not None)
                    for frame in pieces:
                        columns = config.n2_columns if frame["Gas"].iloc[0] == "N2" else config.o2_columns
                        store.append(frame, file_id, columns)
                    lo, hi = pd.Timestamp(record[3]), pd.Timestamp(record[4])
                    if pd.notna(lo):
                        first = lo if pd.isna(first) else min(first, lo)
                        last = hi if pd.isna(last) else max(last, hi)
                    carry = chunk.iloc[-1:].copy()  # A view would retain the entire chunk.
            if count < 2:
                raise ValueError("시간 컬럼이 없거나 기록이 2행 미만입니다.")
            if not all(tube_ids.values()):
                raise ValueError("Tube ID와 파일명/폴더의 Tube가 일치하지 않습니다.")
            if expected_bounds is not None and pd.notna(first):
                if first.value < expected_bounds[0] or last.value > expected_bounds[1]:
                    raise ValueError("파일명의 8시간 기록 범위를 벗어난 시각이 있습니다. 파일명과 시간 컬럼을 확인하세요.")
            store.finish_file()
            return [str(path), tube, count, str(first), str(last)], issues
        except UnicodeDecodeError:
            store.discard_file()
            continue
        except BaseException:
            store.discard_file()
            raise
    raise ValueError("CSV 인코딩을 읽을 수 없습니다.")


class NeedsDiskOrdering(Exception):
    pass


class SourceChanged(Exception):
    pass


class IntervalTotals:
    def __init__(self, config):
        self.config = config
        self.months = pd.period_range(config.start_month, config.end_month, freq="M")
        self.values = {}

    def add(self, tube, gas, start, end, q0, q1, channels, unique_start=None):
        n = channels.shape[1] // 2
        if unique_start is None:
            unique_start = start
        for month in self.months:
            lo = np.maximum(unique_start, month.start_time.value)
            hi = np.minimum(end, (month + 1).start_time.value)
            keep = hi > lo
            if not keep.any():
                continue
            duration = end[keep] - start[keep]
            left_fraction = (lo[keep] - start[keep]) / duration
            right_fraction = (hi[keep] - start[keep]) / duration
            seconds = (hi[keep] - lo[keep]) / 1e9
            left = q0[keep] + (q1[keep] - q0[keep]) * left_fraction
            right = q0[keep] + (q1[keep] - q0[keep]) * right_fraction
            volume = np.sum((left + right) * .5 * seconds / 60) / CM3_PER_FT3
            c0, c1 = channels[keep, :n], channels[keep, n:]
            left_c = c0 + (c1 - c0) * left_fraction[:, None]
            right_c = c0 + (c1 - c0) * right_fraction[:, None]
            channel_volume = np.sum((left_c + right_c) * .5 * seconds[:, None] / 60, axis=0) / CM3_PER_FT3
            values = np.r_[volume, seconds.sum() / 3600, channel_volume]
            self.values.setdefault((tube, gas, str(month)), np.zeros(n + 2))[:] += values

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
            covered = end if len(group) == 1 else max(covered, end)
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
            covered = np.maximum.accumulate(end)
            prior_end = np.concatenate(([previous_end], covered[:-1]))
            prior_end = np.maximum(prior_end, previous_end)
            unique_start = np.maximum(start, prior_end)
            previous_end = max(previous_end, int(covered[-1]))
            ids, counts = np.unique(files[unique_start > start], return_counts=True)
            for file_id, count in zip(ids, counts):
                issues.append([str(paths[file_id]), f"{gas} 중복/겹침 구간은 한 번만 집계 (시작 시각·파일 경로 순 우선)", count])
            totals.add(tube, gas, start, end, q0, q1, channels, unique_start)
    return totals


def summarize_usage(integrated, identities, config):
    months = [str(m) for m in pd.period_range(config.start_month, config.end_month, freq="M")]
    index = pd.MultiIndex.from_tuples(
        [(month, tube) for month in months for tube in sorted(identities)],
        names=["Month", "Tube"])
    tubes = pd.DataFrame(index=index)
    for gas in ("N2", "O2"):
        subset = integrated[integrated["Gas"] == gas].set_index(["Month", "Tube"])
        tubes[f"{gas} (ft³)"] = subset["Volume"]
        tubes[f"{gas} recorded (h)"] = subset["Hours"]
    tubes["N2+O2 (ft³)"] = tubes["N2 (ft³)"] + tubes["O2 (ft³)"]
    tubes["Status"] = np.where(tubes[VOLUME_COLUMNS[:2]].isna().all(axis=1), "기록 없음",
                              np.where(tubes[VOLUME_COLUMNS[:2]].isna().any(axis=1), "부분 집계", "기록 구간 집계"))
    tubes = tubes.reset_index()
    monthly = tubes.groupby(["Month"], as_index=False)[VOLUME_COLUMNS[:2] + HOUR_COLUMNS].sum(min_count=1)
    monthly["N2+O2 (ft³)"] = monthly["N2 (ft³)"] + monthly["O2 (ft³)"]
    statuses = tubes.groupby("Month")["Status"].agg(
        lambda values: "기록 없음" if (values == "기록 없음").all() else
        ("부분 집계" if (values != "기록 구간 집계").any() else "기록 구간 집계"))
    monthly["Status"] = [statuses.loc[m] for m in monthly["Month"]]
    return (tubes[["Month", "Tube", *VOLUME_COLUMNS, *HOUR_COLUMNS, "Status"]],
            monthly[["Month", *VOLUME_COLUMNS, *HOUR_COLUMNS, "Status"]])


def calculate_monthly_usage(root, config, progress=lambda done, total, name: None,
                            cancelled=lambda: False, *, period_cache=None, cooperate=lambda: None, temp_parent=None):
    config.validate()
    root = Path(root)
    if not root.is_dir():
        raise ValueError("공정 로그 폴더가 존재하지 않습니다.")
    paths = []
    for path in root.rglob("*"):
        if cancelled():
            raise CalculationCancelled()
        if path.is_file() and path.suffix.lower() == ".csv":
            paths.append(path)
    paths.sort()
    if cancelled():
        raise CalculationCancelled()
    issues, records, identities = IssueCounts(), [], set()
    candidates, candidate_bounds, rejected_names = [], [], []
    period_start = pd.Period(config.start_month, "M").start_time.value
    period_end = (pd.Period(config.end_month, "M") + 1).start_time.value
    if config.filename_period:
        for i, path in enumerate(paths):
            if cancelled():
                raise CalculationCancelled()
            if i % 100 == 0:
                progress(i, len(paths), "파일명으로 대상 월 선별 (CSV 내용은 열지 않음)")
            parsed = proc_log_period(path)
            if parsed is None:
                rejected_names.append(str(path))
                issues.append([str(path), "파일명 형식 제외: TUBE##_YYMMDD_0/8/16.csv 형식이 아닙니다", 1])
                continue
            tube, bounds = parsed
            identities.add(tube)
            if period_start <= bounds[0] < period_end:
                candidates.append(path)
                candidate_bounds.append(bounds)

    def current_fingerprint():
        if period_cache is None:
            return None
        if config.filename_period:
            return selection_fingerprint(candidates, identities, rejected_names)
        return source_fingerprint(paths)

    key = query_key(root, config)
    fingerprint = current_fingerprint()
    cached = load_result(period_cache, key, fingerprint)
    if cached is not None:
        if cancelled():
            raise CalculationCancelled()
        result = GasUsageResult(**cached)
        result.settings.loc[result.settings["Setting"] == "Result cache", "Value"] = "Reused (source metadata unchanged)"
        if config.filename_period:
            for setting, value in (("Scanned files", len(paths)),
                                   ("Skipped outside period", len(paths) - len(candidates) - len(rejected_names)),
                                   ("Rejected filename format", len(rejected_names))):
                result.settings.loc[result.settings["Setting"] == setting, "Value"] = value
        progress(len(paths), len(paths), "변경 없는 로그의 집계 결과 재사용")
        return result
    if not config.filename_period:
        index = LogPeriodIndex(period_cache)
        try:
            for i, path in enumerate(paths):
                if cancelled():
                    raise CalculationCancelled()
                progress(i, len(paths), "기록 기간 확인 (이전 형식: 시간 컬럼 확인)")
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
    component_issues = IssueCounts()

    def process_file(store, i):
        path = candidates[i]
        tube = tube_from_path(path)
        if tube is None:
            component_issues.append([str(path), "Tube를 파일명/폴더에서 식별할 수 없어 제외", 1])
            return
        try:
            known = candidate_bounds[i]
            record, file_issues = stage_flow_file(
                store, path, i, tube, config, cancelled, cooperate,
                expected_bounds=known if config.filename_period else None)
            lo, hi = pd.Timestamp(record[3]), pd.Timestamp(record[4])
            if not config.filename_period and known is not None and pd.notna(lo) and (lo.value < known[0] or hi.value > known[1]):
                raise SourceChanged("로그의 기록 기간이 집계 중 변경되었습니다. 다시 집계하세요.")
            component_issues.extend(file_issues)
            records.append(record)
            identities.add(tube)
        except (ValueError, OSError, pd.errors.ParserError) as exc:
            component_issues.append([str(path), f"파일 제외: {exc}", 1])

    try:
        for component in overlap_components(candidates, candidate_bounds):
            if cancelled():
                raise CalculationCancelled()
            component_paths = [candidates[i] for i in component]
            ck = component_key(key, component_paths)
            cf = source_fingerprint(component_paths) if period_cache is not None else None
            cached = load_component(period_cache, ck, cf)
            if cached is not None:
                partial = IntervalTotals(config)
                partial.values = {(tube, gas, month): np.asarray(values, dtype=float)
                                  for tube, gas, month, values in cached["totals"]}
                totals.merge(partial)
                records.extend(cached["records"])
                identities.update(r[1] for r in cached["records"])
                issues.extend(cached["issues"])
                reused_files += len(component)
                completed += len(component)
                progress(completed, len(candidates), "변경 없는 파일의 부분 집계 재사용")
                continue
            component_issues = IssueCounts()
            record_start = len(records)
            partial = None
            if len(component) == 1:
                i = component[0]
                progress(completed, len(candidates), f"유량 집계 · {candidates[i].name}")
                direct = DirectIntervalStore(config)
                try:
                    process_file(direct, i)
                    partial = direct.totals
                    direct_files += 1
                    completed += 1
                except NeedsDiskOrdering:
                    pass  # Retry the entire file, without its discarded partial totals.
            if partial is None:
                with DiskIntervalStore(cancelled, temp_parent=temp_parent) as store:
                    for i in component:
                        progress(completed, len(candidates), f"유량 집계 · {candidates[i].name}")
                        process_file(store, i)
                        disk_files += 1
                        completed += 1
                    progress(completed, len(candidates), "겹친 구간 정리 및 월별 합계 계산 중")
                    partial = integrate_intervals(store, config, component_issues, candidates, cancelled, cooperate)
            totals.merge(partial)
            issues.extend(component_issues)
            if cf is not None and source_fingerprint(component_paths) == cf:
                save_component(period_cache, ck, cf, {
                    "totals": [[*group_key, values.tolist()] for group_key, values in partial.values.items()],
                    "records": records[record_start:], "issues": list(component_issues)})
        progress(len(candidates), len(candidates), "월별 합계 계산 중")
        if cancelled():
            raise CalculationCancelled()
        integrated, channels = totals.frames()
    except sqlite3.OperationalError:
        if cancelled():
            raise CalculationCancelled() from None
        raise
    tubes, monthly = summarize_usage(integrated, identities, config)
    settings = pd.DataFrame([
        ["Root", str(root.resolve())], ["Months", f"{config.start_month} ~ {config.end_month}"],
        ["File selection", "Filename TUBE##_YYMMDD_0/8/16; select start date in query; eight-hour windows"
         if config.filename_period else "Legacy mode: actual timestamp bounds intersect query"],
        ["Scanned files", len(paths)], ["Candidate files", len(candidates)],
        ["Skipped outside period", len(paths) - len(candidates) - len(rejected_names)],
        ["Rejected filename format", len(rejected_names)],
        ["Processing", f"Streaming {CSV_CHUNK_ROWS} rows per batch; isolated ordered files integrate directly; disk ordering only for overlaps"],
        ["Direct streaming files", direct_files], ["Disk ordering files", disk_files],
        ["Reused files", reused_files],
        ["Result cache", "Fresh calculation"],
        ["Time", config.time_column], ["N2 sccm columns", ", ".join(config.n2_columns)],
        ["O2 sccm columns", ", ".join(config.o2_columns)],
        ["Maximum gap (s)", "Unlimited" if config.max_gap_seconds is None else config.max_gap_seconds],
        ["Formula", "sum((q0+q1)/2 * seconds/60) / 28316.846592"],
        ["Volume basis", "ft³ at the same standard conditions as the source sccm; no T/P correction"],
        ["Scope", "Recorded process intervals only; excludes unlogged idle/purge usage and final-sample extrapolation"],
        ["Overlap", "Count once per Tube/gas in this single-machine log folder; earliest start then file path wins; uncovered tails retained"],
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
    scope = settings.loc[settings["Setting"] == "Months", "Value"].iloc[0]
    settings.loc[settings["Setting"] == "Months", "Value"] = f"{month} ~ {month}"
    settings.loc[len(settings)] = ["Review scan scope", scope]
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
