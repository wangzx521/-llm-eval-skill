"""Synthetic contract tests. No real dataset, model or candidate host execution."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import uuid

from llm_eval_skills.common import EvaluationError, digest, directory_lock, read_json, write_json
from llm_eval_skills.data import load_prepared, save_prepared
from llm_eval_skills.final import extract_final
from llm_eval_skills.report import summarize
from llm_eval_skills.runner import evaluate, generation_config, item_path, request_api
from llm_eval_skills.sandbox import docker_command, inspect_image, score_code
from llm_eval_skills.scoring import Scorer


def item(key="one", target="5", subset="demo", instructions=1):
    return {"id": key, "subset": subset, "sample": {
        "input": [{"role": "user", "content": "Synthetic addition question"}],
        "target": target, "metadata": {"instruction_id_list": ["synthetic"] * instructions,
                                      "kwargs": [{}] * instructions}}}


def response(text="5", reason="stop", **message):
    return {"choices": [{"message": {"content": text, **message}, "finish_reason": reason}],
            "usage": {"completion_tokens": 1}}


class SyntheticScorer:
    def __init__(self, fail_once=False):
        self.fail_once = fail_once
        self.calls = 0

    def preflight(self):
        pass

    def identity(self):
        return {"implementation": "synthetic-test-only"}

    def __call__(self, sample, final):
        self.calls += 1
        if self.fail_once:
            self.fail_once = False
            raise EvaluationError("Synthetic scorer timeout")
        return {"metrics": {"accuracy": float(final["text"] == sample["sample"]["target"])}}


@contextmanager
def mock_api(responses):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            value = responses[min(len(calls) - 1, len(responses) - 1)]
            if isinstance(value, int):
                self.send_response(value)
                self.end_headers()
                return
            payload = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}):
            yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / ("test-work-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.data = self.root / "data"
        self.output = self.root / "output"
        self.scorer = SyntheticScorer()
        self.items = [item(), item("two")]
        save_prepared("gsm8k", self.data, self.items, {"kind": "synthetic"}, {"kind": "test"})

    def tearDown(self):
        shutil.rmtree(self.root)

    def config(self, url="http://127.0.0.1:1/v1", **kwargs):
        return generation_config("gsm8k", "synthetic-model", url, **kwargs)

    def test_mock_api_normal_and_resume_without_repeated_generations(self):
        with mock_api([response()]) as (url, calls):
            result = evaluate(self.data, self.output, self.config(url), scorer=self.scorer)
            self.assertTrue(result["complete"])
            self.assertEqual(result["metrics"]["accuracy"], 1)
            evaluate(None, self.output, mode="resume", scorer=self.scorer)
            self.assertEqual(len(calls), 2)
            self.assertEqual(self.scorer.calls, 2)
            self.assertEqual(calls[0]["n"], 1)
            self.assertNotIn("chat_template_kwargs", calls[0])
            for path in self.output.rglob("*.json"):
                self.assertNotIn("synthetic-key", path.read_text(encoding="utf-8"))

    def test_empty_final_and_truncation_stay_in_denominator(self):
        with mock_api([response(None, reasoning_content="5"), response("5", "length")]) as (url, calls):
            report = evaluate(self.data, self.output, self.config(url), scorer=self.scorer)
            self.assertEqual(report["denominator"], 2)
            self.assertEqual(report["no_final"], 1)
            self.assertEqual(report["truncated"], 1)
            self.assertEqual(report["metrics"]["accuracy"], 0.5)

    def test_api_failure_becomes_unknown_and_is_never_retried(self):
        with mock_api([503]) as (url, calls):
            with self.assertRaises(EvaluationError):
                evaluate(self.data, self.output, self.config(url), scorer=self.scorer)
            with self.assertRaises(EvaluationError):
                evaluate(None, self.output, mode="resume", scorer=self.scorer)
            self.assertEqual(len(calls), 1)
            report = read_json(self.output / "report.json")
            self.assertFalse(report["complete"])
            self.assertIsNone(report["metrics"])
            self.assertEqual(report["denominator"], 2)

    def test_scoring_timeout_recovers_offline_without_api_calls(self):
        self.scorer.fail_once = True
        with mock_api([response()]) as (url, calls):
            with self.assertRaises(EvaluationError):
                evaluate(self.data, self.output, self.config(url, limit=1), scorer=self.scorer)
            result = evaluate(None, self.output, mode="score", scorer=self.scorer)
            self.assertTrue(result["complete"])
            self.assertEqual(len(calls), 1)

    def test_interrupted_inflight_is_unknown(self):
        def interrupted(config, body):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            evaluate(self.data, self.output, self.config(), scorer=self.scorer, transport=interrupted)
        with self.assertRaises(EvaluationError):
            evaluate(None, self.output, mode="resume", scorer=self.scorer)
        self.assertEqual(read_json(item_path(self.output, self.items[0]))["state"], "unknown")

    def test_interruption_after_generation_recovers_by_scoring_only(self):
        class InterruptedScorer(SyntheticScorer):
            def __call__(self, sample, final):
                raise KeyboardInterrupt
        with mock_api([response()]) as (url, calls):
            with self.assertRaises(KeyboardInterrupt):
                evaluate(self.data, self.output, self.config(url, limit=1), scorer=InterruptedScorer())
            result = evaluate(None, self.output, mode="score", scorer=self.scorer)
            self.assertTrue(result["complete"])
            self.assertEqual(len(calls), 1)

    def test_offline_score_stops_at_ungenerated_items(self):
        self.scorer.fail_once = True
        with mock_api([response()]) as (url, calls):
            with self.assertRaises(EvaluationError):
                evaluate(self.data, self.output, self.config(url), scorer=self.scorer)
            with self.assertRaises(EvaluationError):
                evaluate(None, self.output, mode="score", scorer=self.scorer)
            self.assertEqual(len(calls), 1)

    def test_configuration_change_is_rejected(self):
        evaluate(self.data, self.output, self.config(), scorer=self.scorer, transport=lambda *args: response())
        with self.assertRaises(EvaluationError):
            evaluate(self.data, self.output, self.config(temperature=1), mode="resume", scorer=self.scorer)

    def test_data_change_is_rejected(self):
        evaluate(self.data, self.output, self.config(), scorer=self.scorer, transport=lambda *args: response())
        manifest = read_json(self.data / "manifest.json")
        manifest["source"]["revision"] = "changed"
        write_json(self.data / "manifest.json", manifest)
        with self.assertRaises(EvaluationError):
            evaluate(None, self.output, mode="resume", scorer=self.scorer)

    def test_environment_change_is_rejected(self):
        evaluate(self.data, self.output, self.config(), scorer=self.scorer, transport=lambda *args: response())
        self.scorer.identity = lambda: {"implementation": "changed"}
        with self.assertRaises(EvaluationError):
            evaluate(None, self.output, mode="resume", scorer=self.scorer)

    def test_tampered_generation_is_rejected(self):
        evaluate(self.data, self.output, self.config(), scorer=self.scorer, transport=lambda *args: response())
        path = item_path(self.output, self.items[0])
        record = read_json(path)
        record["generation"] = response("altered")
        write_json(path, record)
        with self.assertRaises(EvaluationError):
            evaluate(None, self.output, mode="resume", scorer=self.scorer)

    def test_os_lock_blocks_second_writer(self):
        with directory_lock(self.output):
            with self.assertRaises(EvaluationError):
                with directory_lock(self.output):
                    self.fail("Second writer acquired lock")

    def test_persisted_report_and_csv_exist(self):
        evaluate(self.data, self.output, self.config(), scorer=self.scorer, transport=lambda *args: response())
        for name in ("report.json", "items.csv", "report.md", "config.json", "prepared.json"):
            self.assertTrue((self.output / name).is_file())


class MetricTests(unittest.TestCase):
    def test_final_field_rules(self):
        self.assertEqual(extract_final(response(None, reasoning="5"))["text"], "")
        self.assertEqual(extract_final(response("reason</think>5"), True)["text"], "5")
        self.assertEqual(extract_final(response("<think>5"))["text"], "")
        self.assertEqual(extract_final(response("5"), True)["text"], "")
        self.assertEqual(extract_final(response("5", reasoning_content="reason"), True)["text"], "5")
        self.assertEqual(extract_final({"choices": []})["answer_state"], "missing_choice")

    def test_ceval_micro_and_macro(self):
        items = [item("a", subset="A"), item("b", subset="B"), item("c", subset="B")]
        rows = [{"state": "scored", "score": {"metrics": {"accuracy": v}}} for v in (1, 0, 0)]
        report = summarize("ceval", items, rows)
        self.assertAlmostEqual(report["metrics"]["micro_accuracy"], 1 / 3)
        self.assertEqual(report["metrics"]["macro_accuracy"], 0.5)

    def test_ifeval_instruction_weighting(self):
        items = [item("a", instructions=1), item("b", instructions=3)]
        names = ("prompt_level_strict", "prompt_level_loose", "inst_level_strict", "inst_level_loose")
        rows = [{"state": "scored", "score": {"metrics": dict.fromkeys(names, v),
                 "weights": {"inst_level_strict": n, "inst_level_loose": n}}} for v, n in ((1, 1), (0, 3))]
        report = summarize("ifeval", items, rows)
        self.assertEqual(report["instruction_denominator"], 4)
        self.assertEqual(report["metrics"]["prompt_level_strict"], 0.5)
        self.assertEqual(report["metrics"]["inst_level_strict"], 0.25)

    def test_humaneval_one_candidate_two_suites(self):
        problem = {"base_input": [1], "plus_input": [1, 2]}
        payload = {"code_image": "sha256:" + "a" * 64, "item": {"problem": problem},
                   "final": {"answer_state": "ok"}}
        suites = {"base": {"status": "pass", "passed_cases": 1, "detail_count": 1, "total_cases": 1},
                  "plus": {"status": "fail", "passed_cases": 1, "detail_count": 2, "total_cases": 2}}
        with patch("llm_eval_skills.sandbox.inspect_image", return_value=payload["code_image"]), patch("llm_eval_skills.sandbox.execute", return_value=suites) as execute:
            result = score_code(payload, "synthetic candidate")
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(result["metrics"], {"base_pass@1": 1, "expanded_pass@1": 0})
            self.assertEqual(execute.call_args.args[2]["candidate"], "synthetic candidate")

    def test_code_container_restrictions_are_explicit(self):
        command = docker_command("sha256:" + "a" * 64, "synthetic")
        for required in ("--network=none", "--read-only", "--cap-drop=ALL", "--memory=512m", "--cpus=1", "--pids-limit=32", "--user=65534:65534"):
            self.assertIn(required, command)
        self.assertNotIn("--privileged", command)
        self.assertFalse(any("/var/run/docker.sock" in word for word in command))

    def test_missing_code_image_stops(self):
        with self.assertRaises(EvaluationError):
            inspect_image(None)

    def test_endpoint_and_extra_parameters_validation(self):
        for url in ("https://user:secret@example.test/v1", "https://example.test/v1?key=secret"):
            with self.assertRaises(EvaluationError):
                generation_config("gsm8k", "model", url)
        with self.assertRaises(EvaluationError):
            generation_config("gsm8k", "model", "http://localhost/v1", extra={"api_key": "secret"})

    def test_real_worker_wall_timeout_is_infrastructure_error(self):
        scorer = Scorer("gsm8k", Path.cwd() / ("test-worker-" + uuid.uuid4().hex), timeout=0.05)
        actual_popen = subprocess.Popen
        def sleeping_worker(command, **kwargs):
            return actual_popen([sys.executable, "-c", "import time;time.sleep(10)"], **kwargs)
        try:
            with patch("llm_eval_skills.scoring.subprocess.Popen", side_effect=sleeping_worker):
                with self.assertRaisesRegex(EvaluationError, "timeout"):
                    scorer(item(), {"text": "5", "answer_state": "ok"})
        finally:
            shutil.rmtree(scorer.output)

    def test_worker_interruption_stops_owned_process(self):
        scorer = Scorer("gsm8k", Path.cwd() / ("test-worker-" + uuid.uuid4().hex))
        actual_popen = subprocess.Popen
        processes = []
        def interrupted_worker(command, **kwargs):
            process = actual_popen([sys.executable, "-c", "import time;time.sleep(10)"], **kwargs)
            communicate = process.communicate
            calls = [0]
            def interrupt_once(*args, **options):
                calls[0] += 1
                if calls[0] == 1:
                    raise KeyboardInterrupt
                return communicate(*args, **options)
            process.communicate = interrupt_once
            processes.append(process)
            return process
        try:
            with patch("llm_eval_skills.scoring.subprocess.Popen", side_effect=interrupted_worker):
                with self.assertRaises(KeyboardInterrupt):
                    scorer(item(), {"text": "5", "answer_state": "ok"})
            self.assertIsNotNone(processes[0].poll())
        finally:
            shutil.rmtree(scorer.output)


if __name__ == "__main__":
    unittest.main()
