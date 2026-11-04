"""Denominator-preserving reports from saved, identity-checked evidence."""

from collections import Counter, defaultdict
import csv
import io
import json
from pathlib import Path

from .common import EvaluationError, atomic_bytes, digest, directory_lock, read_json, write_json


def summarize(task, items, records):
    total = len(items)
    scored = [r for r in records if r["state"] == "scored"]
    complete = len(scored) == total and total > 0
    report = {"task": task, "complete": complete, "denominator": total, "scored": len(scored),
              "states": dict(Counter(r["state"] for r in records)), "metrics": None,
              "empty_final": sum(r.get("final", {}).get("answer_state") == "empty_final" for r in records),
              "no_final": sum(r.get("final", {}).get("answer_state") not in (None, "ok") for r in records),
              "truncated": sum(bool(r.get("final", {}).get("truncated")) for r in records)}
    if not complete:
        return report
    names = scored[0]["score"]["metrics"]
    metrics = {}
    for name in names:
        if task == "ifeval" and name.startswith("inst_level_"):
            weights = [r["score"]["weights"][name] for r in records]
            metrics[name] = sum(r["score"]["metrics"][name] * w for r, w in zip(records, weights)) / sum(weights)
        else:
            metrics[name] = sum(r["score"]["metrics"][name] for r in records) / total
    if task == "ifeval":
        report["instruction_denominator"] = sum(len(i["sample"]["metadata"]["instruction_id_list"]) for i in items)
    if task == "ceval":
        subjects = defaultdict(list)
        for item, record in zip(items, records):
            subjects[item["subset"]].append(record["score"]["metrics"]["accuracy"])
        report["subjects"] = {name: {"count": len(values), "accuracy": sum(values) / len(values)} for name, values in subjects.items()}
        metrics = {"micro_accuracy": metrics["accuracy"],
                   "macro_accuracy": sum(v["accuracy"] for v in report["subjects"].values()) / len(subjects)}
    report["metrics"] = metrics
    return report


def build_report(output, locked=False):
    if not locked:
        with directory_lock(output):
            return build_report(output, locked=True)
    from .runner import load_record
    output = Path(output)
    saved = read_json(output / "config.json")
    prepared = read_json(output / "prepared.json")
    items = prepared["items"]
    binding = {"config": saved["config"], "data": saved["data"],
               "selected_items_sha256": digest(items), "scorer": saved["scorer"]}
    if digest(binding) != saved["identity"] or saved["data"] != prepared["manifest"]:
        raise EvaluationError("Saved run identity differs")
    records = [load_record(output, item, saved["identity"]) for item in items]
    report = summarize(saved["config"]["task"], items, records)
    report.update(identity=saved["identity"], protocol=saved["config"]["protocol"],
                  source=prepared["manifest"]["source"], sample_limited=saved["config"]["limit"] is not None)
    write_json(output / "report.json", report)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["id", "subset", "state", "answer_state", "truncated", "metrics"])
    writer.writeheader()
    for item, record in zip(items, records):
        writer.writerow({"id": item["id"], "subset": item["subset"], "state": record["state"],
                         "answer_state": record.get("final", {}).get("answer_state"),
                         "truncated": record.get("final", {}).get("truncated"),
                         "metrics": json.dumps(record.get("score", {}).get("metrics"), ensure_ascii=False)})
    atomic_bytes(output / "items.csv", stream.getvalue().encode("utf-8"))
    lines = [f"# {report['task']} 评测", "", f"完成状态 {report['complete']}",
             f"已评分 {report['scored']} / {report['denominator']}",
             f"无最终回答 {report['no_final']}，截断 {report['truncated']}", ""]
    if report["metrics"] is None:
        lines.append("结果未完成，指标待全部题目评分成功后发布。")
    else:
        lines += ["| 指标 | 分数 |", "|---|---:|"]
        lines += [f"| {name} | {value:.4%} |" for name, value in report["metrics"].items()]
    if "subjects" in report:
        lines += ["", "| 学科 | 题数 | 正确率 |", "|---|---:|---:|"]
        lines += [f"| {name} | {row['count']} | {row['accuracy']:.4%} |" for name, row in report["subjects"].items()]
    atomic_bytes(output / "report.md", ("\n".join(lines) + "\n").encode("utf-8"))
    return report
