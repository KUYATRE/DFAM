"""Integrate recorded gas flow without extending it beyond observed intervals."""

from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np
import pandas as pd


# Exact geometric conversion; retain the standard conditions of the source sccm.
# NIST Handbook 44, Appendix C: 1 ft^3 = 28 316.846 592 cm^3.
CM3_PER_FT3 = 28316.846592
DEFAULT_N2 = ("Low N2(PV)", "High N2(PV)", "POCL N2(PV)", "Door Purge N2(PV)")
DEFAULT_O2 = ("High O2(PV)", "Low O2(PV)")
VOLUME_COLUMNS = ["N2 (ft³)", "O2 (ft³)", "N2+O2 (ft³)"]
HOUR_COLUMNS = ["N2 recorded (h)", "O2 recorded (h)"]
ISSUE_COLUMNS = ["File", "Issue", "Count"]
CHANNEL_COLUMNS = ["Month", "Equipment", "Tube", "Gas", "Channel", "Volume (ft³)", "Recorded (h)"]


class CalculationCancelled(Exception):
    pass


@dataclass(frozen=True)
class GasUsageConfig:
    start_month: str
    end_month: str
    n2_columns: tuple[str, ...] = DEFAULT_N2
    o2_columns: tuple[str, ...] = DEFAULT_O2
    time_column: str = "Time"
    max_gap_seconds: float = 60.0

    def validate(self):
        start, end = pd.Period(self.start_month, "M"), pd.Period(self.end_month, "M")
        if start > end:
            raise ValueError("시작 월은 종료 월보다 늦을 수 없습니다.")
        if end.ordinal - start.ordinal > 119:
            raise ValueError("한 번에 최대 120개월까지 집계할 수 있습니다.")
        if not np.isfinite(self.max_gap_seconds) or self.max_gap_seconds <= 0:
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
    equipment: pd.DataFrame
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


def read_flow_csv(path, config):
    wanted = {config.time_column, *config.n2_columns, *config.o2_columns, "EQP ID", "Tube ID", "TubeID"}
    last_error = None
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            df = pd.read_csv(path, encoding=encoding, low_memory=False,
                             usecols=lambda c: normalize_column(c) in wanted)
            df.columns = [normalize_column(c) for c in df.columns]
            if df.columns.duplicated().any():
                raise ValueError("공백 제거 후 중복되는 컬럼이 있습니다.")
            return df
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"CSV 인코딩을 읽을 수 없습니다: {last_error}")


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


def _label(value):
    return re.sub(r"^([0-9]+)\.0+$", r"\1", str(value).strip())


def flow_intervals(df, path, tube, config, issues):
    """Keep each original adjacent pair; never bridge invalid rows or files."""
    file = str(path)

    def issue(message, count=1):
        if count:
            issues.append([file, message, int(count)])

    if config.time_column not in df or len(df) < 2:
        raise ValueError("시간 컬럼이 없거나 기록이 2행 미만입니다.")
    equipment = "Equipment"
    if "EQP ID" in df:
        ids = df["EQP ID"].dropna().map(_label).unique()
        if len(ids) != 1 or not ids[0]:
            raise ValueError("EQP ID가 없거나 한 파일에 여러 설비가 있습니다.")
        equipment = f"EQP {ids[0]}"
    for column in ("Tube ID", "TubeID"):
        if column in df:
            ids = pd.to_numeric(df[column], errors="coerce").dropna().unique()
            if len(ids) != 1 or ids[0] != int(tube[4:]):
                raise ValueError("Tube ID와 파일명/폴더의 Tube가 일치하지 않습니다.")
    times = parse_times(df[config.time_column])
    issue("잘못되거나 누락된 시각 (인접 구간 제외)", times.isna().sum())
    ns = times.to_numpy(dtype="datetime64[ns]").astype("int64")
    valid_time = times.notna().to_numpy()
    # Compute differences only for valid timestamps, avoiding NaT overflow.
    pair = valid_time[:-1] & valid_time[1:]
    seconds = np.zeros(len(df) - 1)
    seconds[pair] = (ns[1:][pair] - ns[:-1][pair]) / 1e9
    issue("중복 또는 역행 시각 구간 제외", (pair & (seconds <= 0)).sum())
    issue(f"{config.max_gap_seconds:g}초 초과 기록 간격 제외", (pair & (seconds > config.max_gap_seconds)).sum())
    valid = pair & (seconds > 0) & (seconds <= config.max_gap_seconds)
    bounds = (pd.Period(config.start_month, "M").start_time.value,
              (pd.Period(config.end_month, "M") + 1).start_time.value)
    valid &= (ns[:-1] < bounds[1]) & (ns[1:] > bounds[0])
    frames = []
    for gas, columns in (("N2", config.n2_columns), ("O2", config.o2_columns)):
        missing = [c for c in columns if c not in df]
        if missing:
            issue(f"{gas} 집계 제외: 컬럼 누락 ({', '.join(missing)})")
            continue
        values = df[list(columns)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        rates = values.sum(axis=1)
        good = np.isfinite(values).all(axis=1) & (values >= 0).all(axis=1) & np.isfinite(rates)
        issue(f"{gas} 음수/결측/비수치 유량 (인접 구간 제외)", (~good).sum())
        mask = valid & good[:-1] & good[1:]
        if mask.any():
            intervals = pd.DataFrame({"Equipment": equipment, "Tube": tube, "Gas": gas,
                "start": ns[:-1][mask], "end": ns[1:][mask],
                "q0": rates[:-1][mask], "q1": rates[1:][mask], "File": file})
            for index, column in enumerate(columns):
                intervals[f"channel0:{column}"] = values[:-1, index][mask]
                intervals[f"channel1:{column}"] = values[1:, index][mask]
            frames.append(intervals)
    first, last = times.min(), times.max()
    record = [file, equipment, tube, len(df), str(first), str(last)]
    return frames, record


def integrate_intervals(intervals, config, issues, cancelled=lambda: False):
    """Trapezoids, clipped at query/month boundaries and prior covered intervals."""
    rows, channel_rows = [], []
    months = pd.period_range(config.start_month, config.end_month, freq="M")
    for (equipment, tube, gas), group in intervals.groupby(["Equipment", "Tube", "Gas"], sort=True):
        if cancelled():
            raise CalculationCancelled()
        group = group.sort_values(["start", "File", "end"], kind="stable")
        start, end = group["start"].to_numpy(), group["end"].to_numpy()
        q0, q1 = group["q0"].to_numpy(), group["q1"].to_numpy()
        covered = np.maximum.accumulate(end)
        prior_end = np.concatenate(([np.iinfo(np.int64).min], covered[:-1]))
        unique_start = np.maximum(start, prior_end)
        overlap = unique_start > start
        for file, count in group.loc[overlap, "File"].value_counts().items():
            issues.append([file, f"{gas} 중복/겹침 구간은 한 번만 집계 (시작 시각·파일 경로 순 우선)", int(count)])
        for month in months:
            lo = np.maximum(unique_start, month.start_time.value)
            hi = np.minimum(end, (month + 1).start_time.value)
            keep = hi > lo
            if not keep.any():
                continue
            duration = end[keep] - start[keep]
            left = q0[keep] + (q1[keep] - q0[keep]) * ((lo[keep] - start[keep]) / duration)
            right = q0[keep] + (q1[keep] - q0[keep]) * ((hi[keep] - start[keep]) / duration)
            seconds = (hi[keep] - lo[keep]) / 1e9
            volume = np.sum((left + right) * 0.5 * seconds / 60.0) / CM3_PER_FT3
            rows.append([str(month), equipment, tube, gas, float(volume), float(seconds.sum() / 3600)])
            # Use exactly the same valid and deduplicated intervals as the gas total.
            # Independent channel deduplication could otherwise make the cards disagree.
            for channel in config.n2_columns if gas == "N2" else config.o2_columns:
                c0 = group[f"channel0:{channel}"].to_numpy()[keep]
                c1 = group[f"channel1:{channel}"].to_numpy()[keep]
                left_channel = c0 + (c1 - c0) * ((lo[keep] - start[keep]) / duration)
                right_channel = c0 + (c1 - c0) * ((hi[keep] - start[keep]) / duration)
                channel_volume = np.sum((left_channel + right_channel) * 0.5 * seconds / 60) / CM3_PER_FT3
                channel_rows.append([str(month), equipment, tube, gas, channel,
                                     float(channel_volume), float(seconds.sum() / 3600)])
    return (pd.DataFrame(rows, columns=["Month", "Equipment", "Tube", "Gas", "Volume", "Hours"]),
            pd.DataFrame(channel_rows, columns=CHANNEL_COLUMNS))


def summarize_usage(integrated, identities, config):
    months = [str(m) for m in pd.period_range(config.start_month, config.end_month, freq="M")]
    index = pd.MultiIndex.from_tuples(
        [(month, eq, tube) for month in months for eq, tube in sorted(identities)],
        names=["Month", "Equipment", "Tube"])
    tubes = pd.DataFrame(index=index)
    for gas in ("N2", "O2"):
        subset = integrated[integrated["Gas"] == gas].set_index(["Month", "Equipment", "Tube"])
        tubes[f"{gas} (ft³)"] = subset["Volume"]
        tubes[f"{gas} recorded (h)"] = subset["Hours"]
    tubes["N2+O2 (ft³)"] = tubes["N2 (ft³)"] + tubes["O2 (ft³)"]
    tubes["Status"] = np.where(tubes[VOLUME_COLUMNS[:2]].isna().all(axis=1), "기록 없음",
                              np.where(tubes[VOLUME_COLUMNS[:2]].isna().any(axis=1), "부분 집계", "기록 구간 집계"))
    tubes = tubes.reset_index()
    equipment = tubes.groupby(["Month", "Equipment"], as_index=False)[VOLUME_COLUMNS[:2] + HOUR_COLUMNS].sum(min_count=1)
    equipment["N2+O2 (ft³)"] = equipment["N2 (ft³)"] + equipment["O2 (ft³)"]
    statuses = tubes.groupby(["Month", "Equipment"])["Status"].agg(
        lambda values: "기록 없음" if (values == "기록 없음").all() else
        ("부분 집계" if (values != "기록 구간 집계").any() else "기록 구간 집계"))
    equipment["Status"] = [statuses.loc[m, e] for m, e in zip(equipment["Month"], equipment["Equipment"])]
    return (tubes[["Month", "Equipment", "Tube", *VOLUME_COLUMNS, *HOUR_COLUMNS, "Status"]],
            equipment[["Month", "Equipment", *VOLUME_COLUMNS, *HOUR_COLUMNS, "Status"]])


def calculate_monthly_usage(root, config, progress=lambda done, total, name: None,
                            cancelled=lambda: False):
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
    issues, frames, records, identities = [], [], [], set()
    for i, path in enumerate(paths):
        if cancelled():
            raise CalculationCancelled()
        progress(i, len(paths), path.name)
        tube = tube_from_path(path)
        if tube is None:
            issues.append([str(path), "Tube를 파일명/폴더에서 식별할 수 없어 제외", 1])
            continue
        try:
            df = read_flow_csv(path, config)
            pieces, record = flow_intervals(df, path, tube, config, issues)
            frames.extend(pieces)
            records.append(record)
            identities.add((record[1], tube))
        except (ValueError, OSError, pd.errors.ParserError) as exc:
            issues.append([str(path), f"파일 제외: {exc}", 1])
    progress(len(paths), len(paths), "월별 합계 계산 중")
    if cancelled():
        raise CalculationCancelled()
    columns = ["Month", "Equipment", "Tube", "Gas", "Volume", "Hours"]
    integrated, channels = (integrate_intervals(pd.concat(frames, ignore_index=True), config, issues, cancelled)
                            if frames else (pd.DataFrame(columns=columns), pd.DataFrame(columns=CHANNEL_COLUMNS)))
    tubes, equipment = summarize_usage(integrated, identities, config)
    settings = pd.DataFrame([
        ["Root", str(root.resolve())], ["Months", f"{config.start_month} ~ {config.end_month}"],
        ["Time", config.time_column], ["N2 sccm columns", ", ".join(config.n2_columns)],
        ["O2 sccm columns", ", ".join(config.o2_columns)], ["Maximum gap (s)", config.max_gap_seconds],
        ["Formula", "sum((q0+q1)/2 * seconds/60) / 28316.846592"],
        ["Volume basis", "ft³ at the same standard conditions as the source sccm; no T/P correction"],
        ["Scope", "Recorded process intervals only; excludes unlogged idle/purge usage and final-sample extrapolation"],
        ["Overlap", "Count once per equipment/tube/gas; earliest start then file path wins; uncovered tails retained"],
        ["Missing", "Blank is unavailable, not zero. Equipment totals sum available Tube values."],
        ["Recorded hours", "Equipment hours are summed Tube-hours, not elapsed calendar hours"],
    ], columns=["Setting", "Value"])
    return GasUsageResult(tubes, equipment, pd.DataFrame(issues, columns=ISSUE_COLUMNS),
                          pd.DataFrame(records, columns=["File", "Equipment", "Tube", "Rows", "First", "Last"]),
                          settings, channels)


def export_gas_usage(result, path):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in (("Equipment monthly", result.equipment), ("Tube monthly", result.tubes),
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
