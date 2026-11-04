"""Opt-in real dependency and Linux Docker checks, using synthetic questions only."""

import os
from pathlib import Path
import shutil
import unittest
import uuid

from llm_eval_skills.common import EvaluationError
from llm_eval_skills.data import save_prepared
from llm_eval_skills.final import extract_final
from llm_eval_skills.runner import evaluate, generation_config
from llm_eval_skills.scoring import Scorer
from llm_eval_skills.upstream import adapter_for


@unittest.skipUnless(os.environ.get("EVAL_SKILLS_INTEGRATION") == "1", "Set EVAL_SKILLS_INTEGRATION=1 after installing scoring dependencies")
class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / ("test-work-" + uuid.uuid4().hex)
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root)

    def rendered_item(self, task, raw):
        adapter = adapter_for(task, self.root / "work")
        adapter.few_shot_num = 0
        adapter.current_subset_name = "computer_network"
        sample = adapter.record_to_sample(raw)
        adapter.test_dataset = {"computer_network": [sample]}
        adapter._post_process_samples()
        serialized = sample.model_dump(mode="json")
        serialized["input"] = [{"role": m.role, "content": m.content} for m in sample.input]
        return {"id": "synthetic", "subset": "computer_network", "sample": serialized}

    def grade(self, task, item, text, image=None):
        scorer = Scorer(task, self.root / task, timeout=210 if image else 90, code_image=image)
        scorer.preflight()
        scorer.identity()
        final = extract_final({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})
        return scorer(item, final)

    def test_gpqa(self):
        item = self.rendered_item("gpqa", {"Question": "Which synthetic label is correct?", "Correct Answer": "blue",
                                          "Incorrect Answer 1": "red", "Incorrect Answer 2": "green", "Incorrect Answer 3": "yellow"})
        target = item["sample"]["target"]
        self.assertEqual(self.grade("gpqa", item, f"ANSWER: {target}")["metrics"]["accuracy"], 1)

    def test_gsm8k(self):
        item = self.rendered_item("gsm8k", {"question": "Synthetic 2 + 3?", "answer": "2+3=5. #### 5"})
        self.assertEqual(self.grade("gsm8k", item, r"\boxed{5}")["metrics"]["accuracy"], 1)

    def test_math500(self):
        item = self.rendered_item("math500", {"problem": "Synthetic 2 + 3?", "answer": "5", "level": 1,
                                             "solution": "2+3=5", "unique_id": "synthetic"})
        self.assertEqual(self.grade("math500", item, r"\boxed{5}")["metrics"]["accuracy"], 1)

    def test_ceval(self):
        item = self.rendered_item("ceval", {"id": 1, "question": "合成题，2+3等于几？", "A": "5", "B": "4", "C": "6", "D": "7", "answer": "A"})
        self.assertEqual(self.grade("ceval", item, "答案：A")["metrics"]["accuracy"], 1)

    def test_ifeval(self):
        item = self.rendered_item("ifeval", {"key": 1, "prompt": "Include the word synthetic.",
                                            "instruction_id_list": ["keywords:existence"], "kwargs": [{"keywords": ["synthetic"]}]})
        result = self.grade("ifeval", item, "synthetic")
        self.assertEqual(result["metrics"]["prompt_level_strict"], 1)
        self.assertEqual(result["weights"]["inst_level_strict"], 1)

    def test_full_cli_path_with_saved_synthetic_data(self):
        item = self.rendered_item("gsm8k", {"question": "Synthetic 2+3?", "answer": "#### 5"})
        data = self.root / "prepared"
        save_prepared("gsm8k", data, [item], {"kind": "synthetic"}, {"kind": "test"})
        config = generation_config("gsm8k", "synthetic-model", "http://127.0.0.1:1/v1")
        def synthetic_transport(config, body):
            return {"choices": [{"message": {"content": r"\boxed{5}"}, "finish_reason": "stop"}]}
        result = evaluate(data, self.root / "output", config, transport=synthetic_transport)
        self.assertEqual(result["metrics"]["accuracy"], 1)
        self.assertTrue(evaluate(None, self.root / "output", mode="score")["complete"])

    @unittest.skipUnless(os.environ.get("EVAL_CODE_IMAGE"), "Set EVAL_CODE_IMAGE to the built immutable image ID")
    def test_humaneval_real_container_and_outer_timeout(self):
        from llm_eval_skills.sandbox import execute, qualify_image
        image = os.environ["EVAL_CODE_IMAGE"]
        qualify_image(image)
        problem = {"task_id": "Synthetic/0", "prompt": 'def double(x):\n    """Return twice x."""\n',
                   "canonical_solution": "    return x * 2\n", "entry_point": "double", "test": "",
                   "base_input": [[1]], "plus_input": [[2], [3]], "atol": 0}
        item = self.rendered_item("humaneval", problem)
        item["problem"] = problem
        result = self.grade("humaneval", item, "def double(x):\n    return x * 2\n", image)
        self.assertEqual(result["metrics"], {"base_pass@1": 1, "expanded_pass@1": 1})
        result = self.grade("humaneval", item, "def double(x):\n    return 2\n", image)
        self.assertEqual(result["metrics"], {"base_pass@1": 1, "expanded_pass@1": 0})
        with self.assertRaises(EvaluationError):
            execute(image, "import time;time.sleep(20)", {}, timeout=0.3)


if __name__ == "__main__":
    unittest.main()
