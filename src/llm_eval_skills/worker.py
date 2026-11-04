"""One offline, bounded scoring process per saved response."""

import json
import os
from pathlib import Path
import sys
import signal


def score(payload):
    from .common import EvaluationError, validate_score
    from .upstream import adapter_for
    from evalscope.api.dataset import Sample
    from evalscope.api.evaluator import TaskState

    task, item, final = payload["task"], payload["item"], payload["final"]
    adapter = adapter_for(task, payload["work_dir"])
    sample = Sample.model_validate(item["sample"])
    state = TaskState(model="offline-scorer", sample=sample)
    prediction = final["text"] if final["answer_state"] == "ok" else ""
    extracted = adapter.extract_answer(prediction, state)
    result = {"extracted": extracted, "implementation": "evalscope==1.14.0"}
    if task in ("gsm8k", "math500"):
        from evalscope.metrics.nlp.metrics import Accuracy
        specs = adapter.benchmark_meta.metric_list
        if len(specs) != 1 or not isinstance(specs[0], dict):
            raise EvaluationError("Numeric accuracy protocol changed")
        name, options = next(iter(specs[0].items()))
        if name not in ("acc", "accuracy") or options.get("numeric") is not True:
            raise EvaluationError("Numeric accuracy protocol changed")
        result["metrics"] = {"accuracy": Accuracy(numeric=True)(prediction=extracted, reference=sample.target)}
        result["implementation"] += ":Accuracy(numeric=True)"
    elif task == "humaneval":
        from .sandbox import score_code
        result.update(score_code(payload, sample.metadata["prompt"] + extracted))
    else:
        value = adapter.match_score(prediction, extracted, sample.target, state)
        if getattr(value.status, "value", value.status) != "success" or not value.value:
            raise EvaluationError("Upstream scorer produced no successful metrics")
        if task == "ifeval":
            result["metrics"] = dict(value.value)
            result["weights"] = (value.metadata or {}).get("metric_weights")
        else:
            result["metrics"] = {"accuracy": value.value["accuracy"]}
    validate_score(task, item, result)
    return result


def main():
    os.environ.update(HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1",
                      LITELLM_LOCAL_MODEL_COST_MAP="True", CUDA_VISIBLE_DEVICES="",
                      OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)

    def offline(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.bind"):
            raise RuntimeError("Offline scoring forbids network access")

    sys.addaudithook(offline)
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (30, 31))
    payload = json.load(sys.stdin)
    try:
        result = score(payload)
        # Third-party libraries can emit logs to stdout. A separate durable result is authoritative.
        from .common import write_json
        write_json(payload["result_path"], result)
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
