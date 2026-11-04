"""Durable records and identity validation shared by all six skills."""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import shutil
import uuid

TASKS = {
    "gpqa": ("gpqa_diamond", "gpqa", 8192),
    "gsm8k": ("gsm8k", "gsm8k", 4096),
    "math500": ("math_500", "math_500", 8192),
    "ceval": ("ceval", "ceval", 2048),
    "humaneval": ("humaneval", "humaneval", 4096),
    "ifeval": ("ifeval", "ifeval", 4096),
}
IFEVAL_METRICS = {
    "prompt_level_strict", "prompt_level_loose", "inst_level_strict", "inst_level_loose"
}


class EvaluationError(RuntimeError):
    """A protocol or infrastructure error that must not become a wrong answer."""


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_bytes(path, encode(value) + b"\n")


@contextmanager
def scratch_directory(parent):
    """Task-owned scratch directory with inherited workspace permissions."""
    path = Path(parent) / ("score-" + uuid.uuid4().hex)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


@contextmanager
def directory_lock(directory):
    """OS lock releases after crashes, while its sentinel remains reusable."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise EvaluationError("This directory is already in use") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def unit_value(value):
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise EvaluationError("Scorer returned a missing or invalid metric")
    return float(value)


def validate_score(task, item, score):
    if task == "ifeval":
        if set(score["metrics"]) != IFEVAL_METRICS:
            raise EvaluationError("Incomplete IFEval metric set")
        count = len(item["sample"]["metadata"]["instruction_id_list"])
        expected = {key: count for key in ("inst_level_strict", "inst_level_loose")}
        if count <= 0 or score.get("weights") != expected:
            raise EvaluationError("IFEval instruction weights differ from prepared data")
    else:
        wanted = {"base_pass@1", "expanded_pass@1"} if task == "humaneval" else {"accuracy"}
        if set(score["metrics"]) != wanted:
            raise EvaluationError("Incomplete metric set")
    for value in score["metrics"].values():
        unit_value(value)
