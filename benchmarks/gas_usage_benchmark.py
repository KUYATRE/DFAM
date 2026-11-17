"""Windows standalone benchmark: process memory, not whole-PC RAM.

python benchmarks/gas_usage_benchmark.py ROOT --start 2026-01 --end 2026-03 --cache build/benchmark.sqlite3
Repeat in a fresh process to measure result reuse. --background includes the
production scheduling mode and 5 ms yield between batches.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from background_work import background_resources
from gas_usage import GasUsageConfig, calculate_monthly_usage


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This memory counter is Windows-only.")
    config = GasUsageConfig(args.start, args.end)
    start = time.perf_counter()
    if args.background:
        with background_resources():
            result = calculate_monthly_usage(args.root, config, period_cache=args.cache,
                                             cooperate=lambda: time.sleep(.005))
    else:
        result = calculate_monthly_usage(args.root, config, period_cache=args.cache)
    seconds = time.perf_counter() - start
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    settings = result.settings.set_index("Setting")["Value"]
    print(json.dumps({
        "seconds": round(seconds, 3),
        "peak_working_set_mib": round(counters.PeakWorkingSetSize / 1024**2, 2),
        "peak_commit_mib": round(counters.PeakPagefileUsage / 1024**2, 2),
        "contributing_files": len(result.files),
        "direct_files": settings["Direct streaming files"],
        "disk_files": settings["Disk ordering files"],
        "reused_files": settings["Reused files"],
        "result_cache": settings["Result cache"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
