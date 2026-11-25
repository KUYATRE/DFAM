import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtCore import QDate, QSettings, QEventLoop, QTimer, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from gas_usage import (
    CM3_PER_FT3, DEFAULT_N2, DEFAULT_O2, CalculationCancelled, GasUsageConfig,
    calculate_monthly_usage, export_gas_usage, select_usage_month,
)
from gas_usage_dialog import GasUsageDialog


def log(times, n2=CM3_PER_FT3, o2=CM3_PER_FT3, tube=1, equipment=1):
    frame = pd.DataFrame({"Time": times, "EQP ID": equipment, "Tube ID": tube})
    for columns, values in ((DEFAULT_N2, n2), (DEFAULT_O2, o2)):
        for col in columns:
            frame[col] = np.asarray(values) / len(columns)
    return frame


class GasUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = GasUsageConfig("2026-01", "2026-02", filename_period=False)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, frame, name="TUBE01_recipe_1_20260101_000000.csv", **kwargs):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, **kwargs)
        return path

    def result(self, config=None):
        return calculate_monthly_usage(self.root, config or self.config)

    def test_one_cubic_foot_per_minute_and_only_actual_channels(self):
        frame = log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"])
        for name in ["Low N2(SP)", "UTIL-N2(Psi)", "N2 Total [sccm]", "OffSet Low N2"]:
            frame[name] = 1e9
        self.write(frame)
        result = self.result()
        row = result.tubes.iloc[0]
        self.assertAlmostEqual(row["N2 (ft³)"], 1)
        self.assertAlmostEqual(row["O2 (ft³)"], 1)
        self.assertAlmostEqual(row["N2+O2 (ft³)"], 2)
        self.assertAlmostEqual(row["N2 recorded (h)"], 1 / 60)
        self.assertTrue(pd.isna(result.tubes.iloc[1]["N2 (ft³)"]))
        self.assertEqual(result.tubes.iloc[1]["Status"], "No data")

    def test_irregular_sampling_uses_trapezoids_not_row_count(self):
        self.write(log(["2026-01-01 00:00:00", "2026-01-01 00:00:15", "2026-01-01 00:01:00"],
                       n2=[0, CM3_PER_FT3, CM3_PER_FT3], o2=0))
        self.assertAlmostEqual(self.result().tubes.iloc[0]["N2 (ft³)"], 0.875)

    def test_month_boundary_interpolates_rate_and_filters_partial_interval(self):
        self.write(log(["2026-01-31 23:59:30", "2026-02-01 00:00:30"],
                       n2=[0, CM3_PER_FT3 * 2]))
        result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 0.25)
        self.assertAlmostEqual(result.tubes.iloc[1]["N2 (ft³)"], 0.75)
        feb = self.result(GasUsageConfig("2026-02", "2026-02", filename_period=False))
        self.assertAlmostEqual(feb.tubes.iloc[0]["N2 (ft³)"], 0.75)

    def test_leap_year_and_year_boundary(self):
        self.write(log(["2028-02-29 23:59:30", "2028-03-01 00:00:30"]))
        result = self.result(GasUsageConfig("2028-02", "2028-03", filename_period=False))
        self.assertEqual(result.tubes["Month"].tolist(), ["2028-02", "2028-03"])
        self.assertTrue(np.allclose(result.tubes["N2 (ft³)"], [0.5, 0.5]))
        self.write(log(["2028-12-31 23:59:30", "2029-01-01 00:00:30"]), name="TUBE01_year.csv")
        result = self.result(GasUsageConfig("2028-12", "2029-01", filename_period=False))
        self.assertTrue(np.allclose(result.tubes["N2 (ft³)"], [0.5, 0.5]))

    def test_equipment_ids_are_ignored_and_overlaps_deduplicated_per_tube(self):
        times = ["2026-01-01 00:00:00", "2026-01-01 00:01:00"]
        self.write(log(times, equipment=[1, 2]))
        self.write(log(times, tube=2, n2=CM3_PER_FT3 * 3), name="TUBE02_run.csv")
        self.write(log(times, equipment=2), name="second/TUBE01_run.csv")
        result = self.result()
        jan = result.monthly.query("Month == '2026-01'").iloc[0]
        self.assertAlmostEqual(jan["N2 (ft³)"], 4)
        self.assertAlmostEqual(jan["O2 (ft³)"], 2)
        for frame in (result.tubes, result.monthly, result.channels, result.files):
            self.assertNotIn("Equipment", frame.columns)
        self.assertEqual(len(result.tubes.query("Month == '2026-01'")), 2)

    def test_duplicate_and_partial_overlaps_count_only_uncovered_tails(self):
        first = log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"])
        self.write(first, name="TUBE01_a.csv")
        self.write(first, name="copy/TUBE01_a.csv")
        self.write(log(["2026-01-01 00:00:30", "2026-01-01 00:01:30"],
                       n2=CM3_PER_FT3 * 2), name="TUBE01_b.csv")
        result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 2)
        self.assertAlmostEqual(result.tubes.iloc[0]["O2 (ft³)"], 1.5)
        self.assertTrue(result.issues["Issue"].str.contains("duplicate or overlapping").any())

    def test_invalid_gas_endpoints_are_excluded_not_clamped_or_bridged(self):
        frame = log(pd.date_range("2026-01-01", periods=4, freq="1min").astype(str))
        frame.loc[1, DEFAULT_N2[0]] = -1
        self.write(frame)
        result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 1)
        self.assertAlmostEqual(result.tubes.iloc[0]["O2 (ft³)"], 3)
        self.assertTrue(result.issues["Issue"].str.contains("negative").any())

    def test_nan_infinity_and_missing_channel_are_unavailable_not_zero(self):
        frame = log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"])
        frame.loc[0, DEFAULT_N2[0]] = np.inf
        frame = frame.drop(columns=DEFAULT_O2[0])
        self.write(frame)
        result = self.result()
        self.assertTrue(result.tubes[["N2 (ft³)", "O2 (ft³)"]].isna().all().all())
        self.assertEqual(len(result.issues), 2)

    def test_timestamp_gaps_bad_rows_and_files_are_not_bridged(self):
        self.write(log(["2026-01-01 00:00:00", "bad", "2026-01-01 00:01:00", "2026-01-01 00:03:00"]))
        self.write(log(["2026-01-01 00:04:00", "2026-01-01 00:05:00"]), name="TUBE01_second.csv")
        limited = self.result(GasUsageConfig("2026-01", "2026-02", max_gap_seconds=60, filename_period=False))
        self.assertAlmostEqual(limited.tubes.iloc[0]["N2 (ft³)"], 1)
        self.assertTrue(limited.issues["Issue"].str.contains("exceeds 60 seconds").any())

    def test_same_duration_at_one_thirty_and_120_second_cadences(self):
        for interval in (1, 30, 120):
            with self.subTest(interval=interval):
                times = pd.date_range("2026-01-01", periods=240 // interval + 1, freq=f"{interval}s")
                self.write(log(times.astype(str)))
                result = self.result()
                self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 4)
                self.assertAlmostEqual(result.tubes.iloc[0]["N2 recorded (h)"], 240 / 3600)
                self.assertEqual(result.settings.set_index("Setting").loc["Maximum gap (s)", "Value"], "Unlimited")

    def test_mixed_cadences_integrate_a_linear_ramp_using_actual_seconds(self):
        elapsed = np.array([0, 1, 31, 121, 240])
        times = pd.Timestamp("2026-01-01") + pd.to_timedelta(elapsed, unit="s")
        self.write(log(times.astype(str), n2=CM3_PER_FT3 * elapsed / 240))
        row = self.result().tubes.iloc[0]
        self.assertAlmostEqual(row["N2 (ft³)"], 2)
        self.assertAlmostEqual(row["O2 (ft³)"], 4)

    def test_long_interval_crossing_month_boundary_is_split_by_elapsed_time(self):
        self.write(log(["2026-01-31 23:59:00", "2026-02-01 00:02:00"],
                       n2=[0, 3 * CM3_PER_FT3]))
        result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 0.5)
        self.assertAlmostEqual(result.tubes.iloc[1]["N2 (ft³)"], 4)
        self.assertAlmostEqual(result.tubes["N2 recorded (h)"].sum(), 180 / 3600)

    def test_numeric_time_and_backwards_time_are_not_calendar_intervals(self):
        self.write(log([0, 60]))
        self.assertTrue(self.result().tubes["N2 (ft³)"].isna().all())
        self.write(log(["2026-01-01 00:01:00", "2026-01-01 00:00:00"]))
        self.assertTrue(self.result().tubes["N2 (ft³)"].isna().all())

    def test_compact_time_cp949_uppercase_and_tube_directory(self):
        frame = log(["20260101-00:00:00", "20260101-00:01:00"])
        frame["설명"] = "가열"
        self.write(frame, name="TUBE01/run.CSV", encoding="cp949")
        self.assertAlmostEqual(self.result().tubes.iloc[0]["N2 (ft³)"], 1)

    def test_conflicting_tube_and_unknown_file_report_errors(self):
        self.write(log(["2026-01-01", "2026-01-01 00:01:00"], tube=2))
        self.write(log(["2026-01-01", "2026-01-01 00:01:00"]), name="unknown.csv")
        result = self.result()
        self.assertTrue(result.tubes.empty)
        self.assertEqual(len(result.issues), 2)

    def test_empty_folder_and_cancellation(self):
        result = self.result()
        self.assertTrue(result.tubes.empty)
        self.assertTrue(result.monthly.empty)
        with self.assertRaises(CalculationCancelled):
            calculate_monthly_usage(self.root, self.config, cancelled=lambda: True)

    def test_config_validation(self):
        for config in [GasUsageConfig("2026-02", "2026-01", filename_period=False),
                       GasUsageConfig("2026-01", "2026-02", max_gap_seconds=0, filename_period=False),
                       GasUsageConfig("2026-01", "2026-02", n2_columns=("x",), o2_columns=("x",), filename_period=False)]:
            with self.assertRaises(ValueError):
                config.validate()

    def test_excel_has_numeric_volumes_settings_and_review(self):
        self.write(log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"]))
        result = self.result()
        path = self.root / "result.xlsx"
        export_gas_usage(result, path)
        with pd.ExcelFile(path) as book:
            self.assertEqual(len(book.sheet_names), 6)
            self.assertIn("Monthly totals", book.sheet_names)
            self.assertNotIn("Equipment monthly", book.sheet_names)
            frame = pd.read_excel(book, sheet_name="Tube monthly")
            self.assertAlmostEqual(frame.iloc[0]["N2 (ft³)"], 1)
            self.assertTrue(pd.isna(frame.iloc[1]["N2 (ft³)"]))
            self.assertIn("Formula", pd.read_excel(book, sheet_name="Settings")["Setting"].tolist())
            channels = pd.read_excel(book, sheet_name="Channel monthly")
            self.assertAlmostEqual(channels.query("Gas == 'N2'")["Volume (ft³)"].sum(), 1)

    def test_october_reads_flow_only_for_october_and_labels_the_scan_separately(self):
        for month in (8, 9, 10):
            self.write(log([f"2026-{month:02d}-01 00:00:00", f"2026-{month:02d}-01 00:01:00"]),
                       name=f"TUBE01_run_2026{month:02d}01.csv")
        from gas_usage import read_flow_csv
        events = []
        with patch("gas_usage.read_flow_csv", wraps=read_flow_csv) as reader:
            result = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-10", filename_period=False),
                                             progress=lambda *args: events.append(args))
        self.assertEqual([c.args[0].name for c in reader.call_args_list], ["TUBE01_run_20261001.csv"])
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 1)
        self.assertEqual(result.tubes["Month"].tolist(), ["2026-10"])
        settings = result.settings.set_index("Setting")["Value"]
        self.assertEqual(settings["Skipped outside period"], 2)
        self.assertEqual(len(result.files), 1)
        self.assertTrue(any("Checking recording dates" in e[2] for e in events))
        self.assertEqual([e[2] for e in events if "Integrating flow" in e[2]], ["Integrating flow · TUBE01_run_20261001.csv"])

    def test_file_selection_uses_contents_and_keeps_cross_month_intervals(self):
        # Both the file name and the final row alone would exclude this file incorrectly.
        self.write(log(["2026-09-30 23:59:00", "2026-10-01 00:01:00", "2026-09-30 23:59:00"]),
                   name="TUBE01_recipe_20260801.csv")
        result = self.result(GasUsageConfig("2026-10", "2026-10", filename_period=False))
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 1)
        self.assertTrue(result.issues["Issue"].str.contains("Backward timestamp").any())
        self.assertEqual(len(result.files), 1)

    def test_period_index_reuses_unchanged_files_and_rechecks_modified_files(self):
        from gas_usage import read_time_bounds
        cache = self.root / "periods.sqlite3"
        path = self.write(log(["2026-08-01 00:00:00", "2026-08-01 00:01:00"]))
        config = GasUsageConfig("2026-10", "2026-10", filename_period=False)
        first = calculate_monthly_usage(self.root, config, period_cache=cache)
        self.assertTrue(first.files.empty)
        with patch("gas_usage.read_time_bounds", wraps=read_time_bounds) as probe:
            calculate_monthly_usage(self.root, config, period_cache=cache)
            probe.assert_not_called()
            previous = path.stat().st_mtime_ns
            self.write(log(["2026-10-01 00:00:00", "2026-10-01 00:01:00"]))
            os.utime(path, ns=(previous + 1_000_000, previous + 1_000_000))
            updated = calculate_monthly_usage(self.root, config, period_cache=cache)
            probe.assert_called_once()
        self.assertAlmostEqual(updated.tubes.iloc[0]["N2 (ft³)"], 1)

    def test_period_index_is_separate_for_time_columns_and_cache_failure_is_safe(self):
        frame = log(["2026-08-01 00:00:00", "2026-08-01 00:01:00"])
        frame["Alternate"] = ["2026-10-01 00:00:00", "2026-10-01 00:01:00"]
        self.write(frame)
        cache = self.root / "periods.sqlite3"
        original = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-10", filename_period=False), period_cache=cache)
        alternate = GasUsageConfig("2026-10", "2026-10", time_column="Alternate", filename_period=False)
        selected = calculate_monthly_usage(self.root, alternate, period_cache=cache)
        self.assertTrue(original.files.empty)
        self.assertAlmostEqual(selected.tubes.iloc[0]["N2 (ft³)"], 1)
        broken = self.root / "broken.sqlite3"
        broken.write_bytes(b"invalid database")
        recovered = calculate_monthly_usage(self.root, alternate, period_cache=broken)
        pd.testing.assert_frame_equal(recovered.tubes, selected.tubes)

    def test_unreadable_time_metadata_keeps_file_for_diagnostics(self):
        frame = log(["bad", "bad"])
        self.write(frame)
        result = self.result()
        self.assertFalse(result.files.empty)
        self.assertTrue(result.issues["Issue"].str.contains("Invalid or missing timestamp").any())

    def test_streaming_keeps_boundary_pairs_and_bounds_chunk_memory(self):
        from gas_usage import flow_intervals
        self.write(log(pd.date_range("2026-01-01", periods=11, freq="1s").astype(str)))
        with patch("gas_usage.CSV_CHUNK_ROWS", 3), patch("gas_usage.flow_intervals", wraps=flow_intervals) as chunks:
            result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 10 / 60)
        self.assertEqual(result.files.iloc[0]["Rows"], 11)
        self.assertGreater(len(chunks.call_args_list), 1)
        self.assertLessEqual(max(len(c.args[0]) for c in chunks.call_args_list), 4)
        self.assertEqual(result.settings.set_index("Setting").loc["Disk ordering files", "Value"], 0)

    def test_single_row_chunks_split_month_boundary_without_extrapolation(self):
        self.write(log(["2026-01-31 23:59:30", "2026-02-01 00:00:30"], n2=[0, 2 * CM3_PER_FT3]))
        with patch("gas_usage.CSV_CHUNK_ROWS", 1):
            result = self.result()
        self.assertTrue(np.allclose(result.tubes["N2 (ft³)"], [.25, .75]))

    def test_carried_invalid_row_is_reported_once_and_never_bridged(self):
        frame = log(pd.date_range("2026-01-01", periods=5, freq="1min").astype(str))
        frame.loc[2, DEFAULT_N2[0]] = -1
        self.write(frame)
        with patch("gas_usage.CSV_CHUNK_ROWS", 3):
            result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 2)
        self.assertAlmostEqual(result.tubes.iloc[0]["O2 (ft³)"], 4)
        bad = result.issues[result.issues["Issue"].str.contains("negative")]
        self.assertEqual(bad["Count"].sum(), 1)

    def test_late_tube_mismatch_discards_all_earlier_chunks(self):
        frame = log(pd.date_range("2026-01-01", periods=6, freq="1min").astype(str))
        frame.loc[5, "Tube ID"] = 2
        self.write(frame)
        with patch("gas_usage.CSV_CHUNK_ROWS", 2):
            result = self.result()
        self.assertTrue(result.tubes.empty)
        self.assertTrue(result.channels.empty)
        self.assertEqual(len(result.issues), 1)

    def test_disk_batches_preserve_overlap_priority_and_covered_end(self):
        for name, seconds, rate in (("a", (0,120), [CM3_PER_FT3,2*CM3_PER_FT3]),
                                    ("b", (30,150), 3*CM3_PER_FT3),
                                    ("c", (60,90), 8*CM3_PER_FT3),
                                    ("d", (125,180), 4*CM3_PER_FT3)):
            times = pd.Timestamp("2026-01-01") + pd.to_timedelta(seconds, unit="s")
            self.write(log(times.astype(str), n2=rate), name=f"TUBE01_{name}.csv")
        with patch("gas_usage.CSV_CHUNK_ROWS", 1):
            result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 6.5)
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 recorded (h)"], 3/60)
        self.assertAlmostEqual(result.channels.query("Gas == 'N2'")["Volume (ft³)"].sum(), 6.5)

    def test_reversed_rows_are_removed_without_disk_sorting(self):
        self.write(log(["2026-01-01 00:00:00", "2026-01-01 00:02:00",
                        "2026-01-01 00:01:00", "2026-01-01 00:03:00"]))
        with patch("gas_usage.CSV_CHUNK_ROWS", 2):
            result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 3)
        self.assertEqual(result.files.iloc[0]["Rows"], 4)
        self.assertEqual(result.settings.set_index("Setting").loc["Disk ordering files", "Value"], 0)

    def test_late_encoding_failure_rolls_back_before_retry(self):
        from contextlib import contextmanager
        from gas_usage import read_flow_csv
        frame = log(pd.date_range("2026-01-01", periods=4, freq="1min").astype(str))
        self.write(frame)
        @contextmanager
        def reader(path, config, encoding):
            if encoding == "utf-8-sig":
                def bad_chunks():
                    yield frame.iloc[:2]
                    raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "late invalid byte")
                yield bad_chunks()
            else:
                with read_flow_csv(path, config, encoding) as actual:
                    yield actual
        with patch("gas_usage.read_flow_csv", side_effect=reader), patch("gas_usage.CSV_CHUNK_ROWS", 2):
            result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 3)
        self.assertEqual(result.files.iloc[0]["Rows"], 4)

    def test_cancel_and_disk_space_failure_remove_temporary_database(self):
        frame = log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"])
        self.write(frame, name="TUBE01_a.csv")
        self.write(frame, name="TUBE01_b.csv")
        scratch = self.root / "scratch"
        scratch.mkdir()
        stop = False
        def progress(done, total, name):
            nonlocal stop
            if "Resolving overlaps" in name:
                stop = True
        with self.assertRaises(CalculationCancelled):
            calculate_monthly_usage(self.root, self.config, progress, lambda: stop, temp_parent=scratch)
        self.assertEqual(list(scratch.iterdir()), [])
        import shutil
        usage = shutil.disk_usage(scratch)
        with patch("gas_usage_storage.shutil.disk_usage", return_value=type(usage)(usage.total,usage.used,0)):
            with self.assertRaisesRegex(RuntimeError, "1 GiB"):
                calculate_monthly_usage(self.root, self.config, temp_parent=scratch)
        self.assertEqual(list(scratch.iterdir()), [])

    def test_result_cache_reuses_exact_values_and_new_overlap_recomputes_only_affected_tube(self):
        from gas_usage import read_flow_csv
        times = ["2026-01-01 00:00:00", "2026-01-01 00:01:00"]
        self.write(log(times), name="TUBE01_a.csv")
        self.write(log(times, tube=2), name="TUBE02_a.csv")
        cache = self.root / "cache.sqlite3"
        original = calculate_monthly_usage(self.root, self.config, period_cache=cache)
        with patch("gas_usage.read_flow_csv", side_effect=AssertionError("Unexpected CSV read")):
            reused = calculate_monthly_usage(self.root, self.config, period_cache=cache)
        for name in ("tubes", "monthly", "channels", "files", "issues"):
            pd.testing.assert_frame_equal(getattr(original,name),getattr(reused,name))
        self.write(log(times), name="TUBE01_copy.csv")
        with patch("gas_usage.read_flow_csv", wraps=read_flow_csv) as reader:
            updated = calculate_monthly_usage(self.root, self.config, period_cache=cache)
        self.assertEqual({c.args[0].name for c in reader.call_args_list}, {"TUBE01_a.csv","TUBE01_copy.csv"})
        self.assertAlmostEqual(updated.monthly.iloc[0]["N2 (ft³)"], 2)
        self.assertTrue(updated.issues["Issue"].str.contains("duplicate or overlapping").any())
        self.assertEqual(updated.settings.set_index("Setting").loc["Reused files","Value"], 1)

    def test_added_disjoint_file_reads_only_new_file_and_deletion_removes_its_usage(self):
        from gas_usage import read_flow_csv
        self.write(log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"]), name="TUBE01_a.csv")
        cache = self.root / "cache.sqlite3"
        calculate_monthly_usage(self.root, self.config, period_cache=cache)
        added = self.write(log(["2026-01-02 00:00:00", "2026-01-02 00:01:00"]), name="TUBE01_b.csv")
        with patch("gas_usage.read_flow_csv", wraps=read_flow_csv) as reader:
            result = calculate_monthly_usage(self.root, self.config, period_cache=cache)
        self.assertEqual([c.args[0].name for c in reader.call_args_list], [added.name])
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 2)
        added.unlink()
        result = calculate_monthly_usage(self.root, self.config, period_cache=cache)
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 1)

    def test_flow_change_invalidates_only_changed_file_and_config_change_recalculates(self):
        from gas_usage import read_flow_csv
        times = ["2026-01-01 00:00:00", "2026-01-01 00:01:00"]
        self.write(log(times), name="TUBE01_a.csv")
        self.write(log(times,tube=2), name="TUBE02_a.csv")
        cache = self.root / "cache.sqlite3"
        calculate_monthly_usage(self.root, self.config, period_cache=cache)
        self.write(log(times,n2=CM3_PER_FT3*2),name="TUBE01_a.csv")
        with patch("gas_usage.read_flow_csv", wraps=read_flow_csv) as reader:
            changed = calculate_monthly_usage(self.root,self.config,period_cache=cache)
        self.assertEqual([c.args[0].name for c in reader.call_args_list],["TUBE01_a.csv"])
        self.assertAlmostEqual(changed.monthly.iloc[0]["N2 (ft³)"],3)
        limited = calculate_monthly_usage(self.root,GasUsageConfig("2026-01","2026-02",max_gap_seconds=30, filename_period=False),period_cache=cache)
        self.assertTrue(limited.tubes["N2 (ft³)"].isna().all())

    @unittest.skipUnless(os.name == "nt", "Windows resource scheduling")
    def test_background_priority_is_restored_even_when_work_fails(self):
        import ctypes
        import threading
        from ctypes import wintypes
        from background_work import background_resources
        observations = []
        def work():
            kernel=ctypes.WinDLL("kernel32")
            kernel.GetCurrentThread.restype=wintypes.HANDLE
            kernel.GetThreadPriority.argtypes=[wintypes.HANDLE]
            kernel.GetThreadPriority.restype=ctypes.c_int
            handle=kernel.GetCurrentThread()
            before=kernel.GetThreadPriority(handle)
            try:
                with background_resources() as enabled:
                    observations.append(enabled)
                    raise RuntimeError("simulated failure")
            except RuntimeError:
                pass
            observations.append(kernel.GetThreadPriority(handle)==before)
        thread=threading.Thread(target=work)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(observations,[True,True])

    def test_filename_month_filter_only_opens_requested_and_adjacent_boundary_shifts(self):
        import gas_usage
        selected = []
        for hour in (0, 8, 16):
            start = pd.Timestamp("2026-10-01") + pd.Timedelta(hours=hour)
            selected.append(self.write(log([str(start), str(start+pd.Timedelta(hours=8))]),
                                       name=f"TUBE01_261001_{hour}.csv"))
        selected.append(self.write(log(["2026-11-01 00:00:00", "2026-11-01 00:00:30"]),
                                   name="TUBE01_261101_0.csv"))
        selected.append(self.write(log(["2026-09-30 23:59:30", "2026-10-01 00:00:00"]),
                                   name="TUBE01_260930_16.csv"))
        for name in ("TUBE01_260930_8.csv", "TUBE01_261101_8.csv", "TUBE01_260801_8.csv"):
            (self.root/name).write_bytes(b"not a readable CSV")
        events = []
        with patch("gas_usage.read_time_bounds", side_effect=AssertionError("Unexpected timestamp probe")), \
             patch("gas_usage.LogPeriodIndex", side_effect=AssertionError("Unexpected period index")), \
             patch("gas_usage.DiskIntervalStore", side_effect=AssertionError("Unexpected disk sort")), \
             patch("gas_usage.read_flow_csv", wraps=gas_usage.read_flow_csv) as reader:
            result = calculate_monthly_usage(self.root,GasUsageConfig("2026-10","2026-10"),
                                             progress=lambda *args: events.append(args))
        self.assertEqual({c.args[0] for c in reader.call_args_list},set(selected))
        self.assertEqual(reader.call_count,5)
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"],1440)
        settings = result.settings.set_index("Setting")["Value"]
        self.assertEqual(settings["Skipped outside period"], 3)
        self.assertEqual(settings["Prior-month boundary files"], 1)
        self.assertEqual(settings["Next-month boundary files"], 1)
        self.assertTrue(result.issues.empty)
        self.assertTrue(any("Selecting months by filename" in event[2] for event in events))

    def test_filename_discovery_uses_directory_entries_and_stats_only_selected_logs(self):
        import gas_usage_cache
        times = ["2026-10-01 00:00:00", "2026-10-01 00:01:00"]
        selected = self.write(log(times), name="nested/TUBE01_261001_0.CSV")
        outside = self.write(log(times), name="nested/TUBE01_260901_0.csv")
        (self.root / "nested" / "not-a-log.csv").mkdir()
        (self.root / ".csv").write_text("not a CSV path suffix")
        (self.root / "readme.txt").write_text("not a log")
        calls = []

        def stamp(path):
            calls.append(path)
            return source_stamp(path)

        source_stamp = gas_usage_cache.source_stamp
        cache = self.root / "usage.sqlite3"
        config = GasUsageConfig("2026-10", "2026-10")
        with patch("gas_usage.source_stamp", side_effect=stamp), \
             patch("gas_usage_cache.source_stamp", side_effect=stamp), \
             patch.object(Path, "is_file", autospec=True, side_effect=Path.is_file) as is_file:
            result = calculate_monthly_usage(self.root, config, period_cache=cache)
        self.assertEqual(calls, [selected, selected, selected])
        self.assertNotIn(outside, calls)
        self.assertTrue(all(call.args[0] == cache for call in is_file.call_args_list))
        self.assertEqual(result.files["File"].tolist(), [str(selected)])
        self.assertEqual(result.settings.set_index("Setting").loc["Scanned files", "Value"], 2)
        self.assertTrue(result.issues.empty)

    def test_filename_csv_chunks_parse_timestamps_once_including_boundary_carry(self):
        import gas_usage
        self.write(log(["2026-10-31 23:59:58", "2026-10-31 23:59:58",
                        "2026-10-31 23:59:57", "invalid",
                        "2026-10-31 23:59:59", "2026-11-01 00:00:00"]),
                   name="TUBE01_261031_16.csv")
        config = GasUsageConfig("2026-10", "2026-11")
        with patch("gas_usage.CSV_CHUNK_ROWS", 2), \
             patch("gas_usage.parse_times", wraps=gas_usage.parse_times) as parse:
            result = calculate_monthly_usage(self.root, config)
        self.assertEqual(parse.call_count, 3)
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 2 / 60)
        self.assertTrue(pd.isna(result.monthly.iloc[1]["N2 (ft³)"]))
        self.assertEqual(result.files.iloc[0]["First"], "2026-10-31 23:59:58")
        self.assertEqual(result.files.iloc[0]["Last"], "2026-11-01 00:00:00")
        self.assertEqual(result.issues["Count"].tolist(), [1, 1, 1])

    def test_partial_cache_batches_writes_and_never_reuses_log_changed_during_calculation(self):
        import gas_usage
        import gas_usage_cache
        times = ["2026-10-01 00:00:00", "2026-10-01 00:01:00"]
        for tube in range(1, 4):
            self.write(log(times, tube=tube), name=f"TUBE{tube:02d}_261001_0.csv")
        config = GasUsageConfig("2026-10", "2026-10")
        cache = self.root / "usage.sqlite3"
        changed = False

        def update_while_calculating(done, total, message):
            nonlocal changed
            if message.endswith("TUBE01_261001_0.csv") and not changed:
                self.write(log(times, n2=2 * CM3_PER_FT3), name="TUBE01_261001_0.csv")
                changed = True

        with patch.object(gas_usage_cache.ComponentCache, "flush", autospec=True,
                          side_effect=gas_usage_cache.ComponentCache.flush) as flush:
            result = calculate_monthly_usage(self.root, config, update_while_calculating, period_cache=cache)
        self.assertTrue(changed)
        self.assertEqual(flush.call_count, 1)
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 4)
        with patch("gas_usage.read_flow_csv", wraps=gas_usage.read_flow_csv) as reader:
            updated = calculate_monthly_usage(self.root, config, period_cache=cache)
        self.assertEqual([c.args[0].name for c in reader.call_args_list], ["TUBE01_261001_0.csv"])
        for attribute in ("tubes", "monthly", "channels", "files", "issues"):
            pd.testing.assert_frame_equal(getattr(result, attribute), getattr(updated, attribute))

    def test_unchanged_component_cache_is_rechecked_before_reuse_if_log_is_live(self):
        import gas_usage
        times = ["2026-10-01 00:00:00", "2026-10-01 00:01:00"]
        for tube in (1, 2):
            self.write(log(times, tube=tube), name=f"TUBE{tube:02d}_261001_0.csv")
        config = GasUsageConfig("2026-10", "2026-10")
        cache = self.root / "usage.sqlite3"
        calculate_monthly_usage(self.root, config, period_cache=cache)
        self.write(log(times, n2=2 * CM3_PER_FT3), name="TUBE01_261001_0.csv")

        def update_other_component(done, total, message):
            if message.endswith("TUBE01_261001_0.csv"):
                self.write(log(times, n2=3 * CM3_PER_FT3, tube=2), name="TUBE02_261001_0.csv")

        with patch("gas_usage.read_flow_csv", wraps=gas_usage.read_flow_csv) as reader:
            updated = calculate_monthly_usage(self.root, config, update_other_component, period_cache=cache)
        self.assertEqual([c.args[0].name for c in reader.call_args_list],
                         ["TUBE01_261001_0.csv", "TUBE02_261001_0.csv"])
        self.assertAlmostEqual(updated.monthly.iloc[0]["N2 (ft³)"], 5)

    def test_proc_filename_validates_calendar_hours_and_padded_names(self):
        from gas_usage import proc_log_period
        for name in ("TUBE01_261109_0.csv","tube01_261109_00.CSV","TUBE01_261109_08.csv","TUBE01_261109_16.csv"):
            tube,(start,end)=proc_log_period(Path(name))
            self.assertEqual(tube,"TUBE01")
            self.assertEqual(end-start,8*3600*10**9)
        for name in ("TUBE01_261032_0.csv","TUBE01_260229_0.csv","TUBE01_261001_24.csv",
                     "TUBE01_261001_2.csv","TUBE01_261001_x.csv","TUBE00_261001_0.csv"):
            self.assertIsNone(proc_log_period(Path(name)))
        self.assertIsNotNone(proc_log_period(Path("TUBE01_280229_16.csv")))

    def test_filename_month_end_and_year_range_keep_actual_elapsed_seconds(self):
        self.write(log(["2026-10-31 23:59:30","2026-11-01 00:00:00"]),name="TUBE01_261031_16.csv")
        october=calculate_monthly_usage(self.root,GasUsageConfig("2026-10","2026-10"))
        self.assertAlmostEqual(october.monthly.iloc[0]["N2 (ft³)"],.5)
        for day in ("2026-12-31","2027-01-01"):
            start=pd.Timestamp(day)+pd.Timedelta(hours=16)
            self.write(log([str(start),str(start+pd.Timedelta(hours=8))]),name=f"TUBE01_{start:%y%m%d}_16.csv")
        across_year=calculate_monthly_usage(self.root,GasUsageConfig("2026-12","2027-01"))
        self.assertEqual(across_year.monthly["Month"].tolist(),["2026-12","2027-01"])
        self.assertTrue(np.allclose(across_year.monthly["N2 (ft³)"],[480,480]))

    def test_invalid_filename_is_reported_without_reading_and_legacy_mode_remains_available(self):
        from gas_usage import read_flow_csv
        self.write(log(["2026-10-01 00:00:00","2026-10-01 00:01:00"]),name="TUBE01_old_recipe.csv")
        with patch("gas_usage.read_flow_csv",side_effect=AssertionError("Unknown names must not be opened")):
            strict=calculate_monthly_usage(self.root,GasUsageConfig("2026-10","2026-10"))
        self.assertTrue(strict.files.empty)
        self.assertTrue(strict.issues["Issue"].str.contains("Filename excluded").all())
        legacy=calculate_monthly_usage(self.root,GasUsageConfig("2026-10","2026-10",filename_period=False))
        self.assertAlmostEqual(legacy.monthly.iloc[0]["N2 (ft³)"],1)

    def test_filename_window_mismatch_removes_only_rows_inside_overlap_group(self):
        self.write(log(["2026-10-01 00:00:00","2026-10-01 00:01:00","2026-10-01 09:00:00"]),
                   name="a/TUBE01_261001_0.csv")
        self.write(log(["2026-10-01 00:00:00","2026-10-01 00:01:00"]),name="b/TUBE01_261001_0.csv")
        with patch("gas_usage.CSV_CHUNK_ROWS",2):
            result=calculate_monthly_usage(self.root,GasUsageConfig("2026-10","2026-10"))
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"],1)
        self.assertEqual(len(result.files),2)
        self.assertTrue(result.issues["Issue"].str.contains("8-hour window").any())

    def test_one_second_hmi_skew_with_variable_cadence_is_single_pass_without_file_gap(self):
        import gas_usage
        early = self.write(log(["2026-10-01 07:59:00", "2026-10-01 07:59:30"]),
                           name="TUBE01_261001_0.csv")
        late = self.write(log(["2026-10-01 07:59:58", "2026-10-01 07:59:59", "2026-10-01 08:00:00",
                               "2026-10-01 08:00:30", "2026-10-01 08:00:37"]),
                          name="TUBE01_261001_8.csv")
        config = GasUsageConfig("2026-10", "2026-10")
        with patch("gas_usage.read_flow_csv", wraps=gas_usage.read_flow_csv) as reader, \
             patch("gas_usage.DiskIntervalStore", side_effect=AssertionError("Unexpected disk sort")):
            result = calculate_monthly_usage(self.root, config)
        self.assertEqual([c.args[0] for c in reader.call_args_list], [early, late])
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], (30 + 1 + 30 + 7) / 60)
        self.assertEqual(result.files.iloc[1]["First"], "2026-10-01 07:59:59")
        self.assertEqual(result.issues["Count"].tolist(), [1])

    def test_next_month_midnight_skew_is_split_by_actual_time_not_counted_twice(self):
        self.write(log(["2026-10-31 23:59:25", "2026-10-31 23:59:55"]),
                   name="TUBE01_261031_16.csv")
        self.write(log(["2026-10-31 23:59:59", "2026-11-01 00:00:29"]),
                   name="TUBE01_261101_0.csv")
        oct_only = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-10"))
        nov_only = calculate_monthly_usage(self.root, GasUsageConfig("2026-11", "2026-11"))
        both = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-11"))
        self.assertAlmostEqual(oct_only.monthly.iloc[0]["N2 (ft³)"], 31 / 60)
        self.assertAlmostEqual(nov_only.monthly.iloc[0]["N2 (ft³)"], 29 / 60)
        expected = pd.concat([oct_only.monthly, nov_only.monthly], ignore_index=True)
        pd.testing.assert_frame_equal(expected, both.monthly)

    def test_prior_shift_midnight_repeats_remain_in_actual_month_and_cache(self):
        import gas_usage
        prior = self.write(log(["2026-10-31 23:59:30", "2026-11-01 00:00:00", "2026-11-01 00:00:00"]),
                           name="TUBE01_261031_16.csv")
        current = self.write(log(["2026-10-31 23:59:59", "2026-11-01 00:00:01"]),
                             name="TUBE01_261101_0.csv")
        (self.root / "TUBE01_261031_8.csv").write_bytes(b"must not read")
        cache = self.root / "usage.sqlite3"
        oct_only = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-10"))
        config = GasUsageConfig("2026-11", "2026-11")
        with patch("gas_usage.read_flow_csv", wraps=gas_usage.read_flow_csv) as reader:
            nov_only = calculate_monthly_usage(self.root, config, period_cache=cache)
        self.assertEqual([c.args[0] for c in reader.call_args_list], [prior, current])
        both = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-11"))
        pd.testing.assert_frame_equal(pd.concat([oct_only.monthly, nov_only.monthly], ignore_index=True),
                                      both.monthly)
        self.assertAlmostEqual(nov_only.monthly.iloc[0]["N2 (ft³)"], 1.5 / 60)
        with patch("gas_usage.read_flow_csv", side_effect=AssertionError("Unexpected warm read")):
            warmed = calculate_monthly_usage(self.root, config, period_cache=cache)
        pd.testing.assert_frame_equal(nov_only.monthly, warmed.monthly)
        self.assertEqual(warmed.settings.set_index("Setting").loc["Prior-month boundary files", "Value"], 1)

    def test_canonical_adjacent_shift_jitter_keeps_both_samples_without_disk_sort(self):
        import gas_usage
        early = self.write(log(["2026-10-01 07:59:30", "2026-10-01 08:00:00"]),
                           name="TUBE01_261001_0.csv")
        late = self.write(log(["2026-10-01 07:59:59", "2026-10-01 08:00:29"], n2=2 * CM3_PER_FT3),
                          name="TUBE01_261001_8.csv")
        config = GasUsageConfig("2026-10", "2026-10")
        with patch("gas_usage.read_flow_csv", wraps=gas_usage.read_flow_csv) as reader, \
             patch("gas_usage.DiskIntervalStore", side_effect=AssertionError("Unexpected disk sort")):
            result = calculate_monthly_usage(self.root, config)
        self.assertEqual([c.args[0] for c in reader.call_args_list], [early, late])
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], (30 + 30 * 2) / 60)
        self.assertEqual(result.settings.set_index("Setting").loc["Direct streaming files", "Value"], 2)
        self.assertTrue(result.issues.empty)

    def test_equal_timestamps_in_separate_normal_shift_files_are_not_deduplicated(self):
        self.write(log(["2026-10-01 07:59:29", "2026-10-01 07:59:59", "2026-10-01 07:59:59"]),
                   name="TUBE01_261001_0.csv")
        self.write(log(["2026-10-01 07:59:59", "2026-10-01 07:59:59", "2026-10-01 08:00:01"]),
                   name="TUBE01_261001_8.csv")
        config = GasUsageConfig("2026-10", "2026-10")
        with patch("gas_usage.DiskIntervalStore", side_effect=AssertionError("Unexpected disk sort")):
            result = calculate_monthly_usage(self.root, config)
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 33 / 60)
        self.assertEqual(result.issues.loc[result.issues.Issue.str.contains("contributes one second"), "Count"].sum(), 2)

    def test_short_gaps_use_one_second_then_use_raw_elapsed_across_chunks(self):
        elapsed = np.array([0, 0, 1, 3, 6, 15, 25, 55, 115])  # Gaps: 0, 1, 2, 3, 9, 10, 30, 60
        times = pd.Timestamp("2026-10-01") + pd.to_timedelta(elapsed, unit="s")
        self.write(log(times.astype(str)), name="TUBE01_261001_0.csv")
        config = GasUsageConfig("2026-10", "2026-10")
        for chunk_size in (1, 2, 3, 10_000):
            with self.subTest(chunk_size=chunk_size), patch("gas_usage.CSV_CHUNK_ROWS", chunk_size):
                result = calculate_monthly_usage(self.root, config)
            self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], (3 + 3 + 9 + 10 + 30 + 60) / 60)
            self.assertAlmostEqual(result.monthly.iloc[0]["N2 recorded (h)"], 115 / 3600)
            self.assertAlmostEqual(result.channels.query("Gas == 'N2'")["Volume (ft³)"].sum(), 115 / 60)

    def test_short_gap_at_month_boundary_prorates_one_second_at_interpolated_rates(self):
        self.write(log(["2026-10-31 23:59:59", "2026-11-01 00:00:01"], n2=[0, 4 * CM3_PER_FT3]),
                   name="TUBE01_261101_0.csv")
        october = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-10"))
        november = calculate_monthly_usage(self.root, GasUsageConfig("2026-11", "2026-11"))
        both = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-11"))
        self.assertTrue(np.allclose(both.monthly["N2 (ft³)"], [0.5/60, 1.5/60]))
        self.assertTrue(np.allclose(both.monthly["N2 recorded (h)"], [0.5/3600, 0.5/3600]))
        pd.testing.assert_frame_equal(pd.concat([october.monthly, november.monthly], ignore_index=True), both.monthly)

    def test_max_gap_uses_raw_time_before_short_gap_weighting(self):
        times = pd.Timestamp("2026-10-01") + pd.to_timedelta([0, 0, 1, 3, 12], unit="s")
        self.write(log(times.astype(str)), name="TUBE01_261001_0.csv")
        result = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-10", max_gap_seconds=2))
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 3 / 60)
        self.assertEqual(result.issues.loc[result.issues.Issue.str.contains("Recording gap"), "Count"].sum(), 1)

    def test_other_month_changes_keep_selected_month_cache_and_new_selected_file_is_read_once(self):
        from gas_usage import read_flow_csv
        self.write(log(["2026-10-01 00:00:00","2026-10-01 00:01:00"]),name="TUBE01_261001_0.csv")
        config=GasUsageConfig("2026-10","2026-10")
        cache=self.root/"cache.sqlite3"
        calculate_monthly_usage(self.root,config,period_cache=cache)
        outside=self.root/"TUBE01_261101_8.csv"
        outside.write_bytes(b"unreadable outside period")
        with patch("gas_usage.read_flow_csv",side_effect=AssertionError("Unrelated month invalidated cache")), \
             patch("gas_usage.read_time_bounds",side_effect=AssertionError("Unexpected time probe")):
            reused=calculate_monthly_usage(self.root,config,period_cache=cache)
        self.assertTrue(reused.settings.set_index("Setting").loc["Result cache","Value"].startswith("Reused"))
        self.assertEqual(reused.settings.set_index("Setting").loc["Scanned files","Value"],2)
        self.assertEqual(reused.settings.set_index("Setting").loc["Skipped outside period","Value"],1)
        added=self.write(log(["2026-10-02 00:00:00","2026-10-02 00:01:00"]),name="TUBE01_261002_0.csv")
        with patch("gas_usage.read_flow_csv",wraps=read_flow_csv) as reader:
            changed=calculate_monthly_usage(self.root,config,period_cache=cache)
        self.assertEqual([c.args[0] for c in reader.call_args_list],[added])
        self.assertAlmostEqual(changed.monthly.iloc[0]["N2 (ft³)"],2)

    def test_same_time_rows_add_one_second_at_each_repeated_rows_rate(self):
        times = ["2026-10-01 00:00:00"] * 3 + ["2026-10-01 00:01:00"]
        self.write(log(times, n2=np.array([0, 3, 6, 9]) * CM3_PER_FT3), name="TUBE01_261001_0.csv")
        config = GasUsageConfig("2026-10", "2026-10")
        for size in (1, 2, 3, 10000):
            with self.subTest(size=size), patch("gas_usage.CSV_CHUNK_ROWS", size), \
                 patch("gas_usage.DiskIntervalStore", side_effect=AssertionError("Unexpected disk sorting")):
                result = calculate_monthly_usage(self.root, config)
            self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 7.5 + 9 / 60)
            self.assertAlmostEqual(result.monthly.iloc[0]["O2 (ft³)"], 62 / 60)
            self.assertAlmostEqual(result.monthly.iloc[0]["N2 recorded (h)"], 62 / 3600)
            self.assertAlmostEqual(result.channels.query("Gas == 'N2'")["Volume (ft³)"].sum(), 7.65)
            repeated = result.issues[result.issues.Issue.str.contains("contributes one second")]
            self.assertEqual(repeated.Count.sum(), 2)

    def test_repeated_samples_survive_disk_overlap_and_duplicate_file_copies(self):
        frame = log(["2026-10-01 00:00:00"] * 5 + ["2026-10-01 00:01:00"])
        for directory in ("a", "b"):
            self.write(frame, name=f"{directory}/TUBE01_261001_0.csv")
        with patch("gas_usage.CSV_CHUNK_ROWS", 1):
            result = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-10"))
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 64 / 60)
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 recorded (h)"], 64 / 3600)
        self.assertTrue(result.issues.Issue.str.contains("identical repeated-timestamp rows").any())

    def test_only_equal_time_files_are_deduplicated_in_legacy_mode(self):
        for name in ("TUBE01_a.csv", "TUBE01_b.csv"):
            self.write(log(["2026-01-01 00:00:00"] * 4), name=name)
        with patch("gas_usage.CSV_CHUNK_ROWS", 1):
            result = self.result()
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 3 / 60)

    def test_invalid_time_barrier_does_not_merge_distinct_repeated_rows(self):
        frame = log(["2026-10-01 00:00:00"] * 2 + ["bad"] + ["2026-10-01 00:00:00"] * 2)
        config = GasUsageConfig("2026-10", "2026-10")
        self.write(frame, name="a/TUBE01_261001_0.csv")
        direct = calculate_monthly_usage(self.root, config)
        self.write(frame, name="b/TUBE01_261001_0.csv")
        for size in (1, 2, 10000):
            with self.subTest(size=size), patch("gas_usage.CSV_CHUNK_ROWS", size):
                disk = calculate_monthly_usage(self.root, config)
            self.assertAlmostEqual(disk.monthly.iloc[0]["N2 (ft³)"], 2 / 60)
            pd.testing.assert_frame_equal(direct.monthly, disk.monthly)

    def test_reverse_rows_are_removed_before_integrating_remaining_rates(self):
        self.write(log(["2026-01-01 00:00:00", "2026-01-01 00:00:30",
                        "2026-01-01 00:00:20", "2026-01-01 00:00:25", "2026-01-01 00:01:00"],
                       n2=np.array([0, 2, 100, 100, 4]) * CM3_PER_FT3))
        for size in (1, 2, 10000):
            with self.subTest(size=size), patch("gas_usage.CSV_CHUNK_ROWS", size):
                result = self.result()
            self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 2)
            self.assertEqual(result.issues.loc[result.issues.Issue.str.contains("Backward timestamp"), "Count"].sum(), 2)

    def test_outside_window_rows_do_not_discard_valid_rows_or_pollute_reverse_filter(self):
        frame = log(["2026-09-30 23:59:00", "2026-10-01 00:00:00", "2026-10-01 09:00:00",
                     "2026-10-01 00:01:00", "2026-10-01 00:02:00", "2026-10-01 09:01:00"],
                    n2=np.array([100, 1, 100, 2, 3, 100]) * CM3_PER_FT3)
        frame.loc[[0, 2, 5], "Tube ID"] = 99  # Excluded rows cannot reject this file either.
        self.write(frame, name="TUBE01_261001_0.csv")
        for size in (1, 2, 10000):
            with self.subTest(size=size), patch("gas_usage.CSV_CHUNK_ROWS", size):
                result = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-10"))
            self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 4)
            self.assertEqual(len(result.files), 1)
            self.assertEqual(result.issues.loc[result.issues.Issue.str.contains("8-hour"), "Count"].sum(), 3)
            self.assertFalse(result.issues.Issue.str.contains("Backward timestamp").any())

    def test_all_outside_rows_produce_no_volume_and_explain_exclusion(self):
        self.write(log(["2026-10-01 09:00:00", "2026-10-01 09:01:00"]), name="TUBE01_261001_0.csv")
        result = calculate_monthly_usage(self.root, GasUsageConfig("2026-10", "2026-10"))
        self.assertTrue(result.monthly["N2 (ft³)"].isna().all())
        self.assertEqual(result.issues.loc[result.issues.Issue.str.contains("8-hour"), "Count"].sum(), 2)

    def test_repeated_rows_are_assigned_to_timestamp_month_without_shifting_later_rows(self):
        self.write(log(["2026-01-31 23:59:59"] * 3 + ["2026-02-01 00:00:00"] * 2))
        with patch("gas_usage.CSV_CHUNK_ROWS", 1):
            result = self.result()
        self.assertTrue(np.allclose(result.monthly["N2 (ft³)"], [3 / 60, 1 / 60]))
        self.assertTrue(np.allclose(result.monthly["N2 recorded (h)"], [3 / 3600, 1 / 3600]))

    def test_repeated_row_uses_own_valid_rate_and_cache_preserves_added_seconds(self):
        frame = log(["2026-10-01 00:00:00"] * 3)
        frame.loc[0, DEFAULT_N2[0]] = -1
        self.write(frame, name="TUBE01_261001_0.csv")
        config = GasUsageConfig("2026-10", "2026-10", max_gap_seconds=.5)
        cache = self.root / "cache.sqlite3"
        result = calculate_monthly_usage(self.root, config, period_cache=cache)
        self.assertAlmostEqual(result.monthly.iloc[0]["N2 (ft³)"], 2 / 60)
        with patch("gas_usage.read_flow_csv", side_effect=AssertionError("Cache not reused")):
            cached = calculate_monthly_usage(self.root, config, period_cache=cache)
        pd.testing.assert_frame_equal(result.monthly, cached.monthly)

    def test_month_export_slices_range_without_mutating_original(self):
        self.write(log(["2026-01-31 23:59:30", "2026-02-01 00:00:30"]))
        result = self.result()
        selected = select_usage_month(result, "2026-02")
        for frame in (selected.tubes, selected.monthly, selected.channels):
            self.assertEqual(set(frame["Month"]), {"2026-02"})
        self.assertEqual(len(result.monthly), 2)
        self.assertEqual(selected.settings.set_index("Setting").loc["Months", "Value"], "2026-02 ~ 2026-02")
        self.assertEqual(result.settings.set_index("Setting").loc["Months", "Value"], "2026-01 ~ 2026-02")

    def test_channel_values_share_overlap_and_month_clipping_with_totals(self):
        self.write(log(["2026-01-31 23:59:30", "2026-02-01 00:00:30"],
                       n2=[0, 2 * CM3_PER_FT3]), name="TUBE01_a.csv")
        later = log(["2026-02-01 00:00:00", "2026-02-01 00:01:00"])
        for column in DEFAULT_N2:
            later[column] = 0
        later[DEFAULT_N2[0]] = CM3_PER_FT3
        self.write(later, name="TUBE01_b.csv")
        result = self.result()
        jan = result.channels.query("Month == '2026-01' and Gas == 'N2'")
        feb = result.channels.query("Month == '2026-02' and Gas == 'N2'")
        self.assertTrue(np.allclose(jan["Volume (ft³)"], 0.0625))
        self.assertAlmostEqual(feb.set_index("Channel").loc[DEFAULT_N2[0], "Volume (ft³)"], 0.6875)
        self.assertAlmostEqual(feb["Volume (ft³)"].sum(), result.tubes.iloc[1]["N2 (ft³)"])
        self.assertAlmostEqual(feb["Volume (ft³)"].sum(), 1.25)


class GasUsageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_filename_filter_is_default_and_reaches_worker_and_saved_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            log(["2026-10-01 00:00:00","2026-10-01 00:01:00"]).to_csv(root/"TUBE01_261001_0.csv",index=False)
            settings=QSettings(str(root/"ui.ini"),QSettings.IniFormat)
            dialog=GasUsageDialog(root,settings=settings)
            try:
                self.assertTrue(dialog.filename_filter.isChecked())
                dialog.selected_month.setDate(QDate(2026,10,1))
                dialog.start_calculation()
                self.assertTrue(dialog.worker.config.filename_period)
                loop=QEventLoop()
                timer=QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(loop.quit)
                dialog.worker.finished.connect(loop.quit)
                timer.start(10000)
                loop.exec()
                timer.stop()
                self.assertIsNone(dialog.worker)
                self.assertAlmostEqual(dialog.result.monthly.iloc[0]["N2 (ft³)"],1)
                self.assertTrue(settings.value("filename_period",type=bool))
                dialog.filename_filter.setChecked(False)
                self.assertIsNone(dialog.result)
                dialog.restore_defaults()
                self.assertTrue(dialog.filename_filter.isChecked())
            finally:
                if dialog.worker is not None:
                    dialog.worker.requestInterruption()
                    dialog.worker.wait()
                    self.app.processEvents()
                dialog.close()
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_close_waits_for_cancellation_before_destroying_worker(self):
        def slow_scan(root, config, progress, cancelled, **kwargs):
            while not cancelled():
                time.sleep(0.001)
            raise CalculationCancelled()

        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "ui.ini"), QSettings.IniFormat)
            dialog = GasUsageDialog(folder, settings=settings)
            dialog.filename_filter.setChecked(False)  # Legacy recipe-log fixtures.
            with patch("gas_usage_dialog.calculate_monthly_usage", side_effect=slow_scan):
                dialog.start_calculation()
                loop = QEventLoop()
                timeout = QTimer()
                timeout.setSingleShot(True)
                timeout.timeout.connect(loop.quit)
                dialog.worker.finished.connect(loop.quit)
                dialog.reject()
                timeout.start(10000)
                loop.exec()
                timeout.stop()
                try:
                    self.assertIsNone(dialog.worker)
                    self.assertIsNone(dialog.result)
                    self.assertIn("cancelled", dialog.status.text())
                finally:
                    if dialog.worker is not None:
                        dialog.worker.requestInterruption()
                        dialog.worker.wait()
                        self.app.processEvents()
                    dialog.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_worker_calculates_filters_and_invalidates_export_after_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"]).to_csv(root / "TUBE01_run.csv", index=False)
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            dialog = GasUsageDialog(root, settings=settings)
            dialog.filename_filter.setChecked(False)  # Legacy recipe-log fixtures.
            dialog.month_from.setDate(QDate(2026, 1, 1))
            dialog.month_to.setDate(QDate(2026, 2, 1))
            dialog.start_calculation(scope="trend")
            loop = QEventLoop()
            dialog.worker.finished.connect(loop.quit)
            timeout = QTimer()
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)
            timeout.start(10000)
            loop.exec()
            timeout.stop()
            try:
                self.assertIsNone(dialog.worker)
                self.assertIsNotNone(dialog.result)
                self.assertEqual(dialog.tube_table.rowCount(), 2)
                self.assertTrue(dialog.export_button.isEnabled())
                dialog.tube_filter.setCurrentIndex(1)
                self.assertIn("1.0000", dialog.summary.text())
                dialog.n2_edit.setText("other")
                self.assertFalse(dialog.export_button.isEnabled())
                self.assertIsNone(dialog.result)
                self.assertEqual(dialog.tube_table.rowCount(), 0)
            finally:
                if dialog.worker is not None:
                    dialog.worker.requestInterruption()
                    dialog.worker.wait()
                    self.app.processEvents()
                dialog.close()
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_month_and_trend_calculations_have_independent_ranges_and_results(self):
        def wait_for_worker(dialog):
            loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            dialog.worker.finished.connect(loop.quit)
            timer.start(10000)
            loop.exec()
            timer.stop()
            self.assertIsNone(dialog.worker)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for month in (1, 2):
                log([f"2026-{month:02d}-01 00:00:00", f"2026-{month:02d}-01 00:01:00"],
                    n2=CM3_PER_FT3 * month).to_csv(root / f"TUBE01_{month}.csv", index=False)
            dialog = GasUsageDialog(root, settings=QSettings(str(root / "ui.ini"), QSettings.IniFormat))
            dialog.filename_filter.setChecked(False)  # Legacy recipe-log fixtures.
            try:
                self.assertEqual(dialog.tabs.count(), 4)
                self.assertFalse(hasattr(dialog, "equipment_filter"))
                dialog.selected_month.setDate(QDate(2026, 2, 1))
                dialog.month_from.setDate(QDate(2026, 1, 1))
                dialog.month_to.setDate(QDate(2026, 2, 1))
                dialog.calculate_button.click()
                self.assertEqual(dialog.worker.config.start_month, "2026-02")
                self.assertEqual(dialog.worker.config.end_month, "2026-02")
                self.assertFalse(dialog.selected_month.isEnabled())
                wait_for_worker(dialog)
                self.assertEqual(dialog.month_result.tubes["Month"].tolist(), ["2026-02"])
                self.assertIsNone(dialog.trend_result)
                self.assertIn("2.000000", dialog.monthly_totals["N2"].text())
                self.assertEqual(dialog.chart.series(), [])
                dialog.tabs.setCurrentIndex(1)
                self.assertFalse(dialog.export_button.isEnabled())
                dialog.trend_calculate_button.click()
                self.assertEqual(dialog.worker.config.start_month, "2026-01")
                self.assertEqual(dialog.worker.config.end_month, "2026-02")
                wait_for_worker(dialog)
                self.assertIn("3.0000", dialog.summary.text())
                trend = dialog.trend_result
                dialog.selected_month.setDate(QDate(2026, 3, 1))
                dialog.calculate_button.click()
                wait_for_worker(dialog)
                self.assertIs(dialog.trend_result, trend)
                self.assertIn("3.0000", dialog.summary.text())
                self.assertIn("—", dialog.monthly_totals["N2"].text())
                dialog.selected_month.setDate(QDate(2026, 2, 1))
                self.assertIn("2.000000", dialog.monthly_totals["N2"].text())
                dialog.month_to.setDate(QDate(2026, 3, 1))
                self.assertIsNone(dialog.trend_result)
                self.assertEqual(dialog.chart.series(), [])
                # The monthly result for March survives changes to the trend dates.
                dialog.selected_month.setDate(QDate(2026, 3, 1))
                self.assertIsNotNone(dialog.result)
                self.assertEqual(dialog.result.tubes["Month"].tolist(), ["2026-03"])
            finally:
                if dialog.worker is not None:
                    dialog.worker.requestInterruption()
                    dialog.worker.wait()
                    self.app.processEvents()
                dialog.close()
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_dashboard_uses_proc_log_directory_independent_of_root(self):
        from main import CsvPlotPanel
        panel = CsvPlotPanel(Path("."))
        panel.root_dir = Path("chosen_process_logs")
        panel.proc_log_dir = Path("separate_gas_logs")
        try:
            with patch("main.GasUsageDialog") as dialog, patch("main.apply_chrome_input_styles"):
                panel.btn_gas_usage.click()
                dialog.assert_called_once_with(panel.proc_log_dir, panel)
                dialog.return_value.exec.assert_called_once()
        finally:
            panel.close()
            panel.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_legacy_default_gap_is_removed_but_new_explicit_limit_is_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "ui.ini"), QSettings.IniFormat)
            settings.setValue("gap", 60)
            dialog = GasUsageDialog(folder, settings=settings)
            dialog.filename_filter.setChecked(False)  # Legacy recipe-log fixtures.
            self.assertEqual(dialog.gap.value(), 0)
            dialog.deleteLater()
            settings.setValue("gap_limit_seconds", 120)
            restored = GasUsageDialog(folder, settings=settings)
            restored.filename_filter.setChecked(False)  # Legacy recipe-log fixtures.
            self.assertEqual(restored.gap.value(), 120)
            restored.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_selected_month_cards_do_not_narrow_the_period_trend(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for month, multiplier in (("01", 1), ("02", 4)):
                for tube in (1, 2):
                    times = [f"2026-{month}-01 00:00:00", f"2026-{month}-01 00:01:00"]
                    log(times, n2=CM3_PER_FT3 * multiplier * tube, tube=tube).to_csv(
                        root / f"TUBE0{tube}_{month}.csv", index=False)
            result = calculate_monthly_usage(root, GasUsageConfig("2026-01", "2026-02", filename_period=False))
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            dialog = GasUsageDialog(root, settings=settings)
            dialog.filename_filter.setChecked(False)  # Legacy recipe-log fixtures.
            try:
                dialog._calculation_scope = "trend"
                dialog.tabs.setCurrentIndex(1)
                dialog.show_result(result)
                dialog.selected_month.setDate(QDate(2026, 2, 1))
                self.assertIn("12.000000", dialog.monthly_totals["N2"].text())
                self.assertIn("1.000000", dialog.tube_cards["TUBE01"].values[DEFAULT_N2[0]].text())
                dialog.selected_month.setDate(QDate(2026, 1, 1))
                self.assertIn("3.000000", dialog.monthly_totals["N2"].text())
                self.assertIn("0.250000", dialog.tube_cards["TUBE01"].values[DEFAULT_N2[0]].text())
                dialog.tube_filter.setCurrentIndex(1)
                self.assertIn("5.0000", dialog.summary.text())
                self.assertIn("3.000000", dialog.monthly_totals["N2"].text())
                bars = dialog.chart.series()[0].barSets()[0]
                self.assertEqual([bars.at(0), bars.at(1)], [1, 4])
                dialog.n2_edit.setText("replacement")
                self.assertIn("—", dialog.monthly_totals["N2"].text())
                self.assertIn("—", dialog.tube_cards["TUBE01"].values["replacement"].text())
            finally:
                dialog.close()
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
