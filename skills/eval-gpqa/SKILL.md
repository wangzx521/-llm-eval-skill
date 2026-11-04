---
name: eval-gpqa
description: 通过已有 OpenAI 兼容 API 评测 GPQA Diamond，支持数据准备、中断恢复、离线评分和选项正确率报告。用户请求该项评测或复核已有输出时使用。
---

# GPQA Diamond 评测

先读取 [对应协议](references/protocol.md)，核对数据身份、评分口径和准备条件。

## 独立依赖与入口

在选定 Python 环境安装共享包 `llm-eval-skills[scoring]`。可从仓库固定提交安装，命令为 `python -m pip install "llm-eval-skills[scoring] @ git+https://github.com/wangzx521/-llm-eval-skill.git@<commit>"`。包需独立安装，单独复制本 skill 即可使用已安装包。

入口为 `scripts/evaluate.py`，内部调用共享包并固定 `--test gpqa`。根据本 skill 安装位置定位该脚本。脚本支持 `prepare/run/resume/score/report`。

```bash
python <skill目录>/scripts/evaluate.py prepare --download --revision main --data-dir <数据目录>
python <skill目录>/scripts/evaluate.py run --data-dir <数据目录> --model <模型名> --base-url <API基地址> --output-dir <输出目录>
python <skill目录>/scripts/evaluate.py resume --output-dir <输出目录>
python <skill目录>/scripts/evaluate.py score --output-dir <输出目录>
python <skill目录>/scripts/evaluate.py report --output-dir <输出目录>
```

从用户请求或现有配置取得 API、模型、数据及输出目录。密钥只通过环境变量 OPENAI_API_KEY 读取。数据准备使用显式 revision，本地预渲染 JSONL 可用 `prepare --local` 导入并提供 source/revision/split。

## 执行和报告

默认单并发、每题一次生成、零自动重试，temperature=0、top_p=1、seed=42。参数覆盖记录在配置。模型专有 thinking 参数仅由显式配置启用。恢复使用原配置与完全一致的数据、评分环境。

遇到 API 或评分基础设施错误立即停止，报告标为未完成。已评分题跳过，已有生成只补评分，结果未知的请求保持 unknown 并暂停。无最终回答与截断保留在分母，reasoning 字段不补答案。

交付 JSON、CSV、Markdown 报告并说明完成状态、样本范围、无最终回答数与截断数。用户要求公开结果时另核对可发布范围。
