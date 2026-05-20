from __future__ import annotations

import ctypes
import os
import platform
from dataclasses import asdict, dataclass

from app.core.config import Settings, get_settings

MINERU_PDF_RENDER_THREADS = "MINERU_PDF_RENDER_THREADS"
MINERU_INTRA_OP_NUM_THREADS = "MINERU_INTRA_OP_NUM_THREADS"
MINERU_INTER_OP_NUM_THREADS = "MINERU_INTER_OP_NUM_THREADS"
MINERU_PROCESSING_WINDOW_SIZE = "MINERU_PROCESSING_WINDOW_SIZE"
MINERU_MODEL_SOURCE = "MINERU_MODEL_SOURCE"
DEFAULT_MINERU_MODEL_SOURCE = "modelscope"

MINERU_TUNABLE_ENV_KEYS = (
    MINERU_PDF_RENDER_THREADS,
    MINERU_INTRA_OP_NUM_THREADS,
    MINERU_INTER_OP_NUM_THREADS,
    MINERU_PROCESSING_WINDOW_SIZE,
)


@dataclass(frozen=True)
class MinerUTuningProfile:
    auto_tune: bool
    cpu_count: int
    available_memory_mb: int | None
    reserved_memory_mb: int | None
    memory_per_thread_mb: int
    core_budget: int
    pdf_render_threads: int
    intra_op_num_threads: int
    inter_op_num_threads: int
    processing_window_size: int

    def to_env(self) -> dict[str, str]:
        return {
            MINERU_PDF_RENDER_THREADS: str(self.pdf_render_threads),
            MINERU_INTRA_OP_NUM_THREADS: str(self.intra_op_num_threads),
            MINERU_INTER_OP_NUM_THREADS: str(self.inter_op_num_threads),
            MINERU_PROCESSING_WINDOW_SIZE: str(self.processing_window_size),
        }

    def to_dict(self) -> dict[str, int | bool | None]:
        return asdict(self)


def get_cpu_count() -> int:
    if hasattr(os, "sched_getaffinity"):
        try:
            return max(1, len(os.sched_getaffinity(0)))
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def get_available_memory_mb() -> int | None:
    system = platform.system().lower()
    if system == "windows":
        return _get_available_memory_mb_windows()
    if system == "linux":
        return _get_available_memory_mb_linux()
    return _get_available_memory_mb_posix()


def _get_available_memory_mb_windows() -> int | None:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys // (1024 * 1024))


def _get_available_memory_mb_linux() -> int | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    return int(parts[1]) // 1024
    except OSError:
        return None
    return None


def _get_available_memory_mb_posix() -> int | None:
    if not hasattr(os, "sysconf"):
        return None
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        return None
    if not isinstance(pages, int) or not isinstance(page_size, int):
        return None
    return int(pages * page_size // (1024 * 1024))


def calculate_mineru_tuning(settings: Settings | None = None) -> MinerUTuningProfile:
    settings = settings or get_settings()
    cpu_count = get_cpu_count()
    available_memory_mb = get_available_memory_mb()
    memory_per_thread_mb = max(512, settings.mineru_memory_per_thread_mb)

    if available_memory_mb is None:
        reserved_memory_mb = None
        memory_limited_threads = cpu_count
    else:
        reserved_memory_mb = settings.mineru_reserved_memory_mb
        if reserved_memory_mb is None:
            reserved_memory_mb = max(1024, int(available_memory_mb * 0.2))
        usable_memory_mb = max(512, available_memory_mb - reserved_memory_mb)
        memory_limited_threads = max(1, usable_memory_mb // memory_per_thread_mb)

    core_budget = max(1, min(cpu_count, memory_limited_threads))
    inter_op_threads = max(1, min(4, core_budget // 4 or 1))

    return MinerUTuningProfile(
        auto_tune=settings.mineru_auto_tune,
        cpu_count=cpu_count,
        available_memory_mb=available_memory_mb,
        reserved_memory_mb=reserved_memory_mb,
        memory_per_thread_mb=memory_per_thread_mb,
        core_budget=core_budget,
        pdf_render_threads=settings.mineru_pdf_render_threads or core_budget,
        intra_op_num_threads=settings.mineru_intra_op_num_threads or core_budget,
        inter_op_num_threads=settings.mineru_inter_op_num_threads or inter_op_threads,
        processing_window_size=settings.mineru_processing_window_size
        or max(2, min(32, core_budget * 2)),
    )


def build_mineru_subprocess_env(
    settings: Settings | None = None,
    base_env: dict[str, str] | None = None,
) -> tuple[dict[str, str], MinerUTuningProfile]:
    settings = settings or get_settings()
    env = dict(os.environ if base_env is None else base_env)
    env.setdefault(MINERU_MODEL_SOURCE, DEFAULT_MINERU_MODEL_SOURCE)
    tuning = calculate_mineru_tuning(settings)
    tuned_env = tuning.to_env()

    explicit_overrides = {
        MINERU_PDF_RENDER_THREADS: settings.mineru_pdf_render_threads,
        MINERU_INTRA_OP_NUM_THREADS: settings.mineru_intra_op_num_threads,
        MINERU_INTER_OP_NUM_THREADS: settings.mineru_inter_op_num_threads,
        MINERU_PROCESSING_WINDOW_SIZE: settings.mineru_processing_window_size,
    }

    for key, value in tuned_env.items():
        if explicit_overrides[key] is not None:
            env[key] = value
        elif settings.mineru_auto_tune:
            env.setdefault(key, value)

    return env, tuning
