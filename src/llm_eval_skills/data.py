"""Explicit data acquisition and immutable prepared manifests."""

from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import urllib.request

from .common import (EvaluationError, TASKS, atomic_bytes, digest, directory_lock,
                     encode, file_hash, read_json, read_jsonl, write_json)
from .upstream import adapter_for, scoring_identity

SOURCES = {
    "gpqa": ("Idavidrein/gpqa", ["gpqa_diamond"], "train"),
    "gsm8k": ("openai/gsm8k", ["main"], "test"),
    "math500": ("HuggingFaceH4/MATH-500", ["default"], "test"),
    "ceval": ("ceval/ceval-exam", None, "val"),
    "ifeval": ("google/IFEval", ["default"], "train"),
    "humaneval": ("evalplus/humanevalplus_release", ["default"], "test"),
}


def validate_items(task, items):
    if not items:
        raise EvaluationError("Prepared data is empty")
    seen = set()
    for item in items:
        key = item.get("id")
        if not isinstance(key, str) or not key or key in seen:
            raise EvaluationError("Item IDs must be unique nonempty strings")
        seen.add(key)
        if not isinstance(item.get("subset"), str) or not item["subset"]:
            raise EvaluationError("Every item needs a subset")
        sample = item["sample"]
        messages = sample["input"]
        if not isinstance(messages, list) or not messages:
            raise EvaluationError("Prepared sample.input must contain text chat messages")
        for message in messages:
            if set(message) != {"role", "content"} or message["role"] not in ("system", "user", "assistant") or not isinstance(message["content"], str):
                raise EvaluationError("Only role/content text messages are supported")
        if "target" not in sample or not isinstance(sample.get("metadata"), dict):
            raise EvaluationError("Sample target and metadata are required")
        if task == "ifeval":
            meta = sample["metadata"]
            if not meta.get("instruction_id_list") or len(meta["instruction_id_list"]) != len(meta["kwargs"]):
                raise EvaluationError("Invalid IFEval constraint metadata")
        if task == "humaneval":
            problem = item.get("problem", {})
            for field in ("task_id", "prompt", "canonical_solution", "entry_point", "base_input", "plus_input", "atol"):
                if field not in problem:
                    raise EvaluationError("HumanEval requires the official EvalPlus problem fields")
            if problem["task_id"] != key or sample["metadata"]["prompt"] != problem["prompt"]:
                raise EvaluationError("HumanEval prompt or task binding differs")
        if task == "ceval" and not item["subset"].strip():
            raise EvaluationError("C-Eval needs subject names")


def save_prepared(task, directory, items, source, renderer):
    directory = Path(directory)
    validate_items(task, items)
    with directory_lock(directory):
        if (directory / "manifest.json").exists():
            raise EvaluationError("Data already prepared. Choose a new data directory")
        atomic_bytes(directory / "items.jsonl", b"".join(encode(item) + b"\n" for item in items))
        manifest = {"schema": 1, "task": task, "source": source, "renderer": renderer,
                    "count": len(items), "item_ids": [item["id"] for item in items],
                    "items_sha256": file_hash(directory / "items.jsonl")}
        write_json(directory / "manifest.json", manifest)
    return manifest


def load_prepared(directory, task=None):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    if manifest.get("schema") != 1 or manifest["task"] not in TASKS:
        raise EvaluationError("Unsupported prepared-data schema")
    if task and task != manifest["task"]:
        raise EvaluationError("Prepared task differs")
    if file_hash(directory / "items.jsonl") != manifest["items_sha256"]:
        raise EvaluationError("Prepared data SHA-256 changed")
    items = read_jsonl(directory / "items.jsonl")
    validate_items(manifest["task"], items)
    if len(items) != manifest["count"] or [item["id"] for item in items] != manifest["item_ids"]:
        raise EvaluationError("Prepared manifest membership differs")
    return manifest, items


def prepare_local(task, local, directory, source, revision, split):
    """Local input is a JSONL of fully rendered samples, documented in docs/data.md."""
    items = read_jsonl(local)
    return save_prepared(task, directory, items,
                         {"kind": "local", "name": source, "revision": revision,
                          "split": split, "input_sha256": file_hash(local)},
                         {"kind": "local-pre-rendered", "protocol": "caller-supplied messages"})


def _identifier(task, subset, sample, index):
    metadata = sample.metadata
    fields = {"humaneval": "task_id", "math500": "question_id", "ifeval": "key"}
    if task in fields:
        return str(metadata[fields[task]])
    if task == "ceval":
        return f"{subset}:{metadata['id']}"
    return str(index)


def _official_problems(directory):
    lock = read_json(Path(__file__).with_name("evalplus-lock.json"))
    request = urllib.request.Request(lock["dataset"]["url"], headers={"User-Agent": "llm-eval-skills/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        compressed = response.read(4 * 1024 * 1024)
    if hashlib.sha256(compressed).hexdigest() != lock["dataset"]["gzip_sha256"]:
        raise EvaluationError("Official EvalPlus archive SHA-256 differs")
    raw = gzip.decompress(compressed)
    if hashlib.sha256(raw).hexdigest() != lock["dataset"]["jsonl_sha256"]:
        raise EvaluationError("Official EvalPlus JSONL SHA-256 differs")
    problems = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if len(problems) != 164 or len({p["task_id"] for p in problems}) != 164:
        raise EvaluationError("Official EvalPlus dataset must have 164 unique tasks")
    return problems, lock


def prepare_remote(task, directory, revision):
    adapter = adapter_for(task, Path(directory) / "work")
    source_id, subsets, split = SOURCES[task]
    fingerprints = {}
    problems = None
    if task == "humaneval":
        if revision != "v0.1.10":
            raise EvaluationError("HumanEval uses fixed release v0.1.10")
        problems, lock = _official_problems(directory)
        datasets = {"default": problems}
        resolved = revision
        fingerprints["official_jsonl_sha256"] = lock["dataset"]["jsonl_sha256"]
    else:
        from huggingface_hub import HfApi
        from datasets import load_dataset

        # Resolve a mutable tag before loading. Every split uses the same commit.
        resolved = HfApi().dataset_info(source_id, revision=revision).sha
        if not re.fullmatch(r"[0-9a-f]{40}", resolved):
            raise EvaluationError("Dataset hub did not resolve a commit SHA")
        subsets = subsets or adapter.subset_list
        datasets = {}
        for subset in subsets:
            adapter.current_subset_name = subset
            data = load_dataset(source_id, subset, revision=resolved)
            datasets[subset] = data[split]
            fingerprints[subset] = {name: value._fingerprint for name, value in data.items()}
            if adapter.few_shot_num and adapter.train_split:
                examples = [adapter.record_to_sample(dict(row)) for row in data[adapter.train_split].select(range(adapter.few_shot_num))]
                if adapter.fewshot_dataset is None:
                    adapter.fewshot_dataset = {}
                adapter.fewshot_dataset[subset] = examples
    samples = defaultdict(list)
    for subset, records in datasets.items():
        adapter.current_subset_name = subset
        for record in records:
            row = dict(record)
            if task == "math500":
                row.setdefault("question_id", row.get("unique_id", len(samples[subset])))
            if task == "ceval":
                row["subject"] = subset
            samples[subset].append(adapter.record_to_sample(row))
    adapter.test_dataset = dict(samples)
    adapter._post_process_samples()
    official = {p["task_id"]: p for p in problems or []}
    items = []
    for subset, values in adapter.test_dataset.items():
        for sample in values:
            key = _identifier(task, subset, sample, len(items))
            serialized = sample.model_dump(mode="json")
            serialized["input"] = [{"role": m.role, "content": m.content} for m in sample.input]
            item = {"id": key, "subset": subset, "sample": serialized}
            if task == "humaneval":
                item["problem"] = official[key]
            items.append(item)
    return save_prepared(task, directory, items,
                         {"kind": "download", "name": source_id, "revision": resolved,
                          "requested_revision": revision, "split": split, "fingerprints": fingerprints},
                         {"kind": "evalscope", "identity": digest(scoring_identity(task)),
                          "few_shot": adapter.few_shot_num, "seed": 42,
                          "fewshot_selection": "first N from pinned train/dev split",
                          "adapter": adapter.__class__.__module__})
