# 本地数据格式

可导入已渲染的 JSONL，每行包含 `id`、`subset` 和 `sample`。`sample.input` 为仅含 role/content 的文本消息数组，`sample.target` 为答案，`sample.metadata` 为适配器需要的元数据。另可保留 EvalScope Sample 的 choices、id 等字段。

```bash
llm-eval prepare --test gsm8k --local examples/synthetic-gsm8k.jsonl --source synthetic --revision v1 --split demo --data-dir data/demo
```

合成示例只用于接口与状态流程检查。运行它仍调用真实适配器评分，模型端由使用者明确指定。

各项必要字段由固定 EvalScope 适配器约定。

| 测试 | target | metadata |
|---|---|---|
| gpqa | 正确选项字母 | correct_answer、incorrect_answers，Sample 中保存 choices |
| gsm8k | 数值字符串 | reasoning 可留空 |
| math500 | 答案字符串 | question_id、solution |
| ceval | 正确选项字母 | id、subject、explanation，Sample 中保存 choices |
| ifeval | 空字符串 | prompt、key、instruction_id_list、kwargs |
| humaneval | canonical_solution | task_id、entry_point、prompt、test |

HumanEval 每行另含 `problem`，保存官方完整记录的 task_id、prompt、canonical_solution、entry_point、base_input、plus_input、atol 等字段。`prepare --download --revision v0.1.10` 自动验证官方压缩包与解压 JSONL 的固定 SHA-256。

导入本地数据时，调用者负责题目与提示词的准确来源。程序记录用户提供的 source/revision/split、输入文件哈希和每条题目 ID。将已准备目录移动后，可通过 resume/score 的 `--data-dir` 指向内容完全一致的新位置。
