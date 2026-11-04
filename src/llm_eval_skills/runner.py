"""Serial request -> durable generation -> score -> durable checkpoint."""

import copy
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .common import (EvaluationError, TASKS, digest, directory_lock, encode,
                     read_json, validate_score, write_json)
from .data import load_prepared
from .final import extract_final


def validate_endpoint(base_url):
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise EvaluationError("API URL must be an http(s) base URL without credentials, query or fragment")
    return base_url.rstrip("/")


def generation_config(task, model, base_url, max_tokens=None, temperature=0, top_p=1,
                      extra=None, thinking=False, api_timeout=600, score_timeout=None,
                      code_image=None, limit=None):
    base_url = validate_endpoint(base_url)
    if not model or (max_tokens is not None and max_tokens <= 0) or (limit is not None and limit <= 0):
        raise EvaluationError("Model and positive token/sample limits are required")
    if not 0 <= temperature <= 2 or not 0 < top_p <= 1 or api_timeout <= 0:
        raise EvaluationError("Invalid generation parameters")
    extra = extra or {}
    # Deliberately narrow, explicit model-specific extension points. No authentication fields.
    allowed = {"chat_template_kwargs", "reasoning_effort", "top_k", "repetition_penalty", "min_p"}
    if not isinstance(extra, dict) or set(extra) - allowed:
        raise EvaluationError("Unsupported extra-body keys. See docs/protocol.md")
    template = extra.get("chat_template_kwargs", {})
    if not isinstance(template, dict) or set(template) - {"enable_thinking"}:
        raise EvaluationError("Only explicit enable_thinking is supported in chat_template_kwargs")
    if "enable_thinking" in template and type(template["enable_thinking"]) is not bool:
        raise EvaluationError("enable_thinking must be boolean")
    if template.get("enable_thinking") is True and not thinking:
        raise EvaluationError("enable_thinking=true also requires --thinking for final extraction")
    body = {"model": model, "temperature": temperature, "top_p": top_p,
            "max_tokens": max_tokens or TASKS[task][2], "n": 1, "stream": False, "seed": 42}
    body.update(extra)
    timeout = score_timeout if score_timeout is not None else (210 if task == "humaneval" else 90)
    if timeout <= 0 or (task == "humaneval" and timeout < 180):
        raise EvaluationError("Positive score timeout required. HumanEval minimum is 180 seconds")
    return {"protocol": "llm-eval-skills-v1", "package_version": __version__, "task": task,
            "base_url": base_url, "body": body, "thinking": thinking,
            "api_timeout": api_timeout, "score_timeout": timeout,
            "code_image": code_image, "limit": limit, "concurrency": 1, "retries": 0}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_api(config, body):
    key = os.environ.get("OPENAI_API_KEY")
    if key is None:
        raise EvaluationError("Set OPENAI_API_KEY in the environment, even for a local API")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    request = urllib.request.Request(config["base_url"] + "/chat/completions", data=encode(body), headers=headers)
    # No retry handler, no credential-bearing redirects, no inherited proxy for loopback.
    parsed = urllib.parse.urlsplit(config["base_url"])
    handlers = [NoRedirect()]
    if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        handlers.append(urllib.request.ProxyHandler({}))
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=config["api_timeout"]) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024:
                raise EvaluationError("API response exceeds 32 MiB")
            result = json.loads(raw)
    except urllib.error.HTTPError as error:
        raise EvaluationError(f"API HTTP {error.code}. Response body omitted") from None
    except (OSError, ValueError):
        raise EvaluationError("API transport or JSON response failure") from None
    if not isinstance(result, dict) or "error" in result or not isinstance(result.get("choices"), list):
        raise EvaluationError("API did not return a chat completion")
    # Keep model output and token accounting only. No headers, cookies or server extensions.
    retained = {key: result[key] for key in ("id", "model", "choices", "usage") if key in result}
    def redact(value):
        if isinstance(value, str):
            return value.replace(key, "[REDACTED]") if key else value
        if isinstance(value, list):
            return [redact(entry) for entry in value]
        if isinstance(value, dict):
            return {name: redact(entry) for name, entry in value.items()}
        return value
    retained = redact(retained)
    return retained


def item_path(output, item):
    return Path(output) / "items" / (digest(item["id"]) + ".json")


def load_record(output, item, identity):
    path = item_path(output, item)
    if not path.exists():
        return {"state": "pending", "id": item["id"], "identity": identity,
                "item_sha256": digest(item)}
    record = read_json(path)
    if record.get("identity") != identity or record.get("id") != item["id"] or record.get("item_sha256") != digest(item):
        raise EvaluationError("Checkpoint identity differs")
    if "generation" in record:
        if record.get("generation_sha256") != digest(record["generation"]):
            raise EvaluationError("Saved generation checksum differs")
    if record["state"] == "scored":
        if "generation" not in record or record.get("score_sha256") != digest(record["score"]):
            raise EvaluationError("Scored checkpoint is incomplete")
        validate_score(read_json(Path(output) / "config.json")["config"]["task"], item, record["score"])
    return record


def _execute(output, config, items, identity, scorer, offline, transport):
    for item in items:
        record = load_record(output, item, identity)
        if record["state"] == "scored":
            continue
        if "generation" not in record:
            if record["state"] in ("inflight", "unknown"):
                record["state"] = "unknown"
                write_json(item_path(output, item), record)
                raise EvaluationError("A request outcome is unknown. Automatic generation is stopped")
            if offline:
                raise EvaluationError("Offline scoring found an item without a saved generation")
            if os.environ.get("OPENAI_API_KEY") is None and transport is request_api:
                raise EvaluationError("Set OPENAI_API_KEY before generation")
            body = copy.deepcopy(config["body"])
            body["messages"] = item["sample"]["input"]
            record.update(state="inflight", request=body, request_sha256=digest(body))
            write_json(item_path(output, item), record)
            started = time.monotonic()
            try:
                generation = transport(config, body)
            except Exception as error:
                record.update(state="unknown", error_kind="api", error_type=type(error).__name__)
                write_json(item_path(output, item), record)
                raise EvaluationError("API request failed. Its outcome remains unknown and will not be retried") from None
            record.update(state="generated", generation=generation,
                          generation_sha256=digest(generation), wall_seconds=time.monotonic() - started)
            write_json(item_path(output, item), record)
        final = extract_final(record["generation"], config["thinking"])
        record["final"] = final
        try:
            value = scorer(item, final)
            validate_score(config["task"], item, value)
        except Exception as error:
            record.update(state="unavailable", error_kind="scoring", error_type=type(error).__name__)
            write_json(item_path(output, item), record)
            raise EvaluationError("Scoring infrastructure unavailable. Generation is preserved for offline recovery") from None
        record.update(state="scored", score=value, score_sha256=digest(value))
        record.pop("error_kind", None)
        record.pop("error_type", None)
        write_json(item_path(output, item), record)


def evaluate(data_dir, output, config=None, mode="run", scorer=None, transport=request_api):
    output = Path(output)
    with directory_lock(output):
        saved_path = output / "config.json"
        if mode == "run" and saved_path.exists():
            raise EvaluationError("Output already exists. Use resume or a new directory")
        if mode != "run" and not saved_path.exists():
            raise EvaluationError("No saved run to resume")
        if saved_path.exists():
            saved = read_json(saved_path)
            if config is not None and config != saved["config"]:
                raise EvaluationError("Run configuration changed")
            config = saved["config"]
            data_dir = data_dir or saved["data_dir"]
        manifest, all_items = load_prepared(data_dir, config["task"])
        items = all_items[:config["limit"]] if config["limit"] else all_items
        if scorer is None:
            from .scoring import Scorer
            scorer = Scorer(config["task"], output / "scoring", config["score_timeout"], config["code_image"])
        scorer.preflight()
        scoring = scorer.identity()
        binding = {"config": config, "data": manifest, "selected_items_sha256": digest(items), "scorer": scoring}
        identity = digest(binding)
        if saved_path.exists():
            if identity != saved["identity"] or read_json(output / "prepared.json") != {"manifest": manifest, "items": items}:
                raise EvaluationError("Data, scoring environment or configuration identity changed")
        else:
            write_json(output / "prepared.json", {"manifest": manifest, "items": items})
            write_json(saved_path, {"config": config, "data_dir": str(Path(data_dir).resolve()),
                                    "scorer": scoring, "data": manifest, "identity": identity})
        from .report import build_report
        try:
            _execute(output, config, items, identity, scorer, mode == "score", transport)
        finally:
            build_report(output, locked=True)
        return read_json(output / "report.json")
