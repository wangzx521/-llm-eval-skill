"""Linux CPU Docker execution. No host candidate execution or bind mounts."""

import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid
import os
import sys

from .common import EvaluationError, digest, encode, read_json


def inspect_image(image):
    if not image or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise EvaluationError("HumanEval scoring requires a local immutable Docker image ID")
    if sys.platform != "linux":
        raise EvaluationError("HumanEval scoring requires a Linux host with Linux Docker")
    try:
        info = json.loads(subprocess.check_output(["docker", "info", "--format", "{{json .}}"], timeout=15))
        image_info = json.loads(subprocess.check_output(["docker", "image", "inspect", image], timeout=15))[0]
    except (OSError, subprocess.SubprocessError, ValueError):
        raise EvaluationError("A working Linux Docker engine and prepared CPU image are required") from None
    if info.get("OSType") != "linux" or image_info.get("Os") != "linux":
        raise EvaluationError("Linux Docker containers are required")
    if not any("seccomp" in str(entry) for entry in info.get("SecurityOptions", [])):
        raise EvaluationError("Docker seccomp protection is required")
    if image_info["Id"] != image:
        raise EvaluationError("Docker image identity differs")
    return image


def docker_command(image, name):
    return ["docker", "run", "--name", name, "--label", "task=llm-eval-skills",
            "--rm", "--init", "--log-driver=none", "--runtime=runc", "--network=none", "--read-only",
            "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--pids-limit=32", "--memory=512m", "--memory-swap=512m", "--cpus=1",
            "--ulimit", "nofile=64:64", "--ulimit", "fsize=1048576:1048576",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--env=OPENBLAS_NUM_THREADS=1", "--env=OMP_NUM_THREADS=1",
            "--env=CUDA_VISIBLE_DEVICES=", "--env=NVIDIA_VISIBLE_DEVICES=void",
            "--entrypoint=python", "-i", image, "-I", "-B", "-c"]


BOOTSTRAP = '''
import contextlib, io, json, os, signal, sys
signal.signal(signal.SIGALRM, lambda *args: os._exit(124))
signal.setitimer(signal.ITIMER_REAL, 145)
os.environ['EVALPLUS_MAX_MEMORY_BYTES'] = '536870912'
from copy import deepcopy
from evalplus.eval import untrusted_check
from evalplus.gen.util import trusted_exec
payload = json.load(sys.stdin)
import hashlib, importlib.util, pathlib
for entry in payload['lock']['scoring_core_files']:
    location = pathlib.Path(importlib.util.find_spec('evalplus').origin).parent.parent / entry['path']
    if hashlib.sha256(location.read_bytes()).hexdigest() != entry['sha256']:
        raise RuntimeError('EvalPlus core differs')
p = payload['problem']
result = {}
for part in ['base', 'plus']:
    inputs = p[part + '_input']
    # Reference stdout is not a result channel.
    with contextlib.redirect_stdout(io.StringIO()):
        expected, times = trusted_exec(p['prompt'] + p['canonical_solution'], inputs, p['entry_point'], record_time=True)
        status, details = untrusted_check('humaneval', payload['candidate'], deepcopy(inputs), p['entry_point'], expected, p['atol'], times, fast_check=False)
    result[part] = {'status': status, 'passed_cases': int(sum(details)), 'total_cases': len(inputs), 'detail_count': len(details)}
print(json.dumps(result))
'''


def execute(image, program, payload, timeout=155):
    name = "llm-eval-" + uuid.uuid4().hex
    command = docker_command(image, name) + [program]
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
        try:
            deadline = time.monotonic() + timeout
            first = True
            while True:
                try:
                    process.communicate(encode(payload) if first else None, timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    first = False
                    if time.monotonic() > deadline or os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > 65536:
                        raise EvaluationError("Code container exceeded its wall clock or output limit")
        except BaseException:
            # Stop only the unique container created by this invocation.
            try:
                subprocess.run(["docker", "stop", "--time", "2", name], capture_output=True, timeout=15, check=False)
            finally:
                process.terminate()
                process.wait(timeout=10)
            raise EvaluationError("Code container interrupted or exceeded its outer deadline") from None
        stdout.seek(0)
        raw = stdout.read(65537)
        if process.returncode != 0 or len(raw) > 65536:
            raise EvaluationError("Code container failed or exceeded its output limit")
        try:
            return json.loads(raw)
        except ValueError:
            raise EvaluationError("Code container returned invalid metrics") from None


def score_code(payload, program):
    image = inspect_image(payload["code_image"])
    item = payload["item"]
    # Even an empty answer requires qualified infrastructure, but executes no candidate.
    if payload["final"]["answer_state"] != "ok":
        return {"metrics": {"base_pass@1": 0, "expanded_pass@1": 0}, "executed": False}
    lock = read_json(Path(__file__).with_name("evalplus-lock.json"))
    result = execute(image, BOOTSTRAP, {"problem": item["problem"], "candidate": program, "lock": lock})
    metrics = {}
    for part, metric in (("base", "base_pass@1"), ("plus", "expanded_pass@1")):
        entry = result[part]
        count = len(item["problem"][part + "_input"])
        if entry["status"] not in ("pass", "fail", "timeout") or entry["total_cases"] != count:
            raise EvaluationError("EvalPlus suite result is incomplete")
        if not 0 <= entry["passed_cases"] <= entry["detail_count"] <= count:
            raise EvaluationError("EvalPlus case counts are invalid")
        if entry["status"] == "pass" and entry["passed_cases"] != count:
            raise EvaluationError("EvalPlus pass without every case observed")
        metrics[metric] = int(entry["status"] == "pass")
    return {"metrics": metrics, "suites": result, "image": image,
            "program_sha256": digest(program), "implementation": "EvalPlus author core 26d6d00"}


QUALIFY = '''
import hashlib, importlib.util, json, os, pathlib, socket
lock = json.load(__import__('sys').stdin)
assert os.geteuid() == 65534
status = pathlib.Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\\t1' in status
assert 'CapEff:\\t0000000000000000' in status
assert 'Seccomp:\\t2' in status
cg = pathlib.Path('/sys/fs/cgroup')
if (cg / 'memory.max').exists():
    assert (cg / 'memory.max').read_text().strip() == '536870912'
    assert (cg / 'pids.max').read_text().strip() == '32'
    quota, period = (cg / 'cpu.max').read_text().split()
else:
    assert (cg / 'memory/memory.limit_in_bytes').read_text().strip() == '536870912'
    assert (cg / 'pids/pids.max').read_text().strip() == '32'
    quota = (cg / 'cpu/cpu.cfs_quota_us').read_text().strip()
    period = (cg / 'cpu/cpu.cfs_period_us').read_text().strip()
assert int(quota) == int(period)
for entry in lock['scoring_core_files']:
    path = pathlib.Path(importlib.util.find_spec('evalplus').origin).parent.parent / entry['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
assert os.environ.get('EVALPLUS_TIMEOUT_PER_TASK') is None
try:
    pathlib.Path('/etc/llm-eval-probe').write_text('x')
except OSError:
    pass
else:
    raise RuntimeError('root filesystem is writable')
try:
    socket.create_connection(('1.1.1.1', 80), timeout=1)
except OSError:
    pass
else:
    raise RuntimeError('network is reachable')
from evalplus.eval import untrusted_check
from evalplus.gen.util import trusted_exec
print(json.dumps({'qualified': True}))
'''


def qualify_image(image):
    inspect_image(image)
    lock = read_json(Path(__file__).with_name("evalplus-lock.json"))
    if execute(image, QUALIFY, lock, timeout=20) != {"qualified": True}:
        raise EvaluationError("Code image qualification failed")
