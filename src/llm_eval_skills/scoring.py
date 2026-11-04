"""Bound scoring time and classify unavailable infrastructure."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .common import EvaluationError, encode, read_json, scratch_directory, validate_score
from .upstream import adapter_for, scoring_identity
from . import __version__


class Scorer:
    def __init__(self, task, output, timeout=90, code_image=None):
        self.task = task
        self.output = Path(output)
        self.timeout = timeout
        self.code_image = code_image

    def identity(self):
        identity = scoring_identity(self.task)
        from .common import file_hash
        identity["package_version"] = __version__
        identity["package_sources"] = {path.name: file_hash(path) for path in sorted(Path(__file__).parent.glob("*.py"))}
        if self.task == "humaneval":
            from .sandbox import inspect_image
            identity["code_image"] = inspect_image(self.code_image)
            identity["evalplus_lock"] = read_json(Path(__file__).with_name("evalplus-lock.json"))
        return identity

    def preflight(self):
        # Verify optional imports and adapter configuration before spending model tokens.
        adapter_for(self.task, self.output / "scorer-work")
        if self.task == "ifeval":
            import nltk
            from evalscope.benchmarks.ifeval import utils  # noqa: F401
            nltk.data.find("tokenizers/punkt_tab/english/")
        if self.task in ("gsm8k", "math500"):
            from evalscope.metrics.nlp.metrics import Accuracy  # noqa: F401
        if self.task == "humaneval":
            from .sandbox import qualify_image
            qualify_image(self.code_image)

    def __call__(self, item, final):
        self.output.mkdir(parents=True, exist_ok=True)
        with scratch_directory(self.output) as temporary:
            result_path = Path(temporary) / "result.json"
            payload = {"task": self.task, "item": item, "final": final,
                       "work_dir": str(self.output / "scorer-work"),
                       "result_path": str(result_path.resolve()), "code_image": self.code_image}
            env = dict(os.environ, CUDA_VISIBLE_DEVICES="", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
            try:
                with tempfile.TemporaryFile() as logs:
                    process = subprocess.Popen([sys.executable, "-m", "llm_eval_skills.worker"],
                                               stdin=subprocess.PIPE, stdout=logs, stderr=logs, env=env)
                    try:
                        process.communicate(encode(payload), timeout=self.timeout)
                    except BaseException:
                        process.terminate()
                        try:
                            process.communicate(timeout=25)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate()
                        raise
            except subprocess.TimeoutExpired:
                raise EvaluationError("Scoring worker timeout. Generation is preserved") from None
            if process.returncode or not result_path.exists():
                raise EvaluationError("Scoring worker unavailable. Check dependencies and local data resources")
            result = read_json(result_path)
            validate_score(self.task, item, result)
            return result
