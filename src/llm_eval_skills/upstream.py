"""Use installed EvalScope adapters. No upstream scoring implementation is vendored."""

import importlib
import importlib.metadata
from pathlib import Path

from .common import EvaluationError, TASKS, file_hash


def adapter_for(task, work_dir):
    if importlib.metadata.version("evalscope") != "1.14.0":
        raise EvaluationError("Install llm-eval-skills[scoring] with EvalScope 1.14.0")
    from evalscope.config import TaskConfig
    from evalscope.api.registry import get_benchmark

    name, module, _ = TASKS[task]
    importlib.import_module(f"evalscope.benchmarks.{module}.{module}_adapter")
    config = TaskConfig(model="offline-scorer", eval_type="openai_api", datasets=[],
                        seed=42, work_dir=str(work_dir), repeats=1, eval_batch_size=1)
    adapter = get_benchmark(name, config=config)
    adapter.benchmark_meta._update({"shuffle": False, "few_shot_random": False, "shuffle_choices": False})
    return adapter


def scoring_identity(task):
    """Hash the installed framework code, including checker and parser modules."""
    version = importlib.metadata.version("evalscope")
    if version != "1.14.0":
        raise EvaluationError("EvalScope 1.14.0 is required")
    distribution = importlib.metadata.distribution("evalscope")
    files = {}
    for entry in distribution.files or []:
        name = str(entry).replace("\\", "/")
        if name.startswith("evalscope/") and name.endswith(".py"):
            files[name] = file_hash(distribution.locate_file(entry))
    if not files:
        raise EvaluationError("Cannot identify EvalScope source files")
    dependencies = {}
    for name in ("sympy", "latex2sympy2_extended", "numpy", "nltk", "langdetect"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    import os
    # NLTK data can change checker behavior independently of package versions.
    nltk_files = {}
    if task == "ifeval":
        import nltk
        for root in nltk.data.path:
            if Path(root).is_dir():
                for path in sorted(Path(root).rglob("*")):
                    if path.is_file():
                        nltk_files[f"{len(nltk_files)}:{path.relative_to(root).as_posix()}"] = file_hash(path)
    return {"evalscope": version, "files": files, "dependencies": dependencies,
            "nltk_data": nltk_files, "python": __import__("sys").version,
            "platform": os.name}
