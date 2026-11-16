# 六组模型评测 Skills

通过已有 OpenAI 兼容 Chat Completions API 完成六组评测。六个 skill 可单独安装，共享 Python 包 `llm_eval_skills`。原创编排代码采用 MIT 许可。

| Skill | CLI 测试名 | 指标 |
|---|---|---|
| eval-gpqa | gpqa | GPQA Diamond 选项正确率 |
| eval-gsm8k | gsm8k | GSM8K 数值答案正确率 |
| eval-math500 | math500 | MATH-500 固定数值评分器正确率 |
| eval-ceval | ceval | C-Eval val 学科成绩、微平均、宏平均 |
| eval-humaneval | humaneval | 同一次生成的 base 和 expanded pass@1 |
| eval-ifeval | ifeval | prompt、instruction 两级 strict/loose 四项指标 |

## 环境与共享依赖

需要 Python 3.10 或更高版本，完整评分建议使用 Linux Python 3.12。先创建独立虚拟环境，再运行下面的命令安装共享包。命令已指定固定的代码版本，便于复现相同的评测流程。

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1
python -m pip install "llm-eval-skills[scoring] @ git+https://github.com/wangzx521/-llm-eval-skill.git@aa7dc79e91e24291bbbdbf0e9cb6281ad49cfcb2"
llm-eval --help
```

也可在下载仓库后运行 `python -m pip install '.[scoring]'`。核心包使用 Python 标准库，`[scoring]` 安装 EvalScope 1.14.0 和数据依赖。IFEval 还需提前准备 NLTK `punkt_tab` 数据，可执行 `python -m nltk.downloader punkt_tab`，评分阶段离线。HumanEval 另需 Linux Docker 和固定 EvalPlus CPU 镜像，详见容器说明。

## 单独安装一个 skill

每个 skill 都包含自己的入口和协议说明。先安装上面的共享包，再选择一个目录复制到目标平台的 skills 目录。以下以 eval-gsm8k 为例，其他五项替换名称即可。

```bash
git clone https://github.com/wangzx521/-llm-eval-skill.git llm-eval-skills
cd llm-eval-skills
# Codex，项目级
mkdir -p .agents/skills
cp -R skills/eval-gsm8k .agents/skills/
# Claude Code，项目级
mkdir -p .claude/skills
cp -R skills/eval-gsm8k .claude/skills/
```

PowerShell 对应使用 `New-Item -ItemType Directory -Force .agents/skills` 和 `Copy-Item -Recurse skills/eval-gsm8k .agents/skills/`。用户级目录分别为 `~/.agents/skills/` 和 `~/.claude/skills/`。Codex 可输入 `$eval-gsm8k`，Claude Code 可输入 `/eval-gsm8k`，再说明数据目录、API 地址、模型名和输出目录。

## 准备与调用

`prepare` 是显式数据获取步骤。revision 可用来源提交 SHA，传入标签时会先解析为提交 SHA。GPQA 使用者须自行满足数据访问条件，HF 凭据由 Hugging Face 工具环境管理。

```bash
llm-eval prepare --test gsm8k --download --revision main --data-dir data/gsm8k
# 通过环境变量设置 OPENAI_API_KEY，密钥不进入命令参数或记录
llm-eval run --test gsm8k --data-dir data/gsm8k --model your-model --base-url http://127.0.0.1:8000/v1 --output-dir outputs/gsm8k --limit 5
llm-eval resume --test gsm8k --output-dir outputs/gsm8k
llm-eval score --test gsm8k --output-dir outputs/gsm8k
llm-eval report --output-dir outputs/gsm8k
```

首次 `run` 需要设置 `OPENAI_API_KEY`，本地免认证接口可设为空字符串。`resume` 继续未发出的任务并补评分。`score` 仅处理保存的生成，遇到缺失生成即停止。`report` 读取已有文件，生成 `report.json`、`items.csv`、`report.md`。`--limit` 是从已准备数据顺序取前 N 题，报告标注样本上限。

六个入口都支持 `prepare/run/resume/score/report`。

```bash
python skills/eval-gpqa/scripts/evaluate.py prepare --download --revision main --data-dir data/gpqa
python skills/eval-gsm8k/scripts/evaluate.py run --data-dir data/gsm8k --model your-model --base-url http://127.0.0.1:8000/v1 --output-dir outputs/gsm8k
python skills/eval-math500/scripts/evaluate.py prepare --download --revision main --data-dir data/math500
python skills/eval-ceval/scripts/evaluate.py prepare --download --revision main --data-dir data/ceval
python skills/eval-humaneval/scripts/evaluate.py prepare --download --revision v0.1.10 --data-dir data/humaneval
python skills/eval-ifeval/scripts/evaluate.py prepare --download --revision main --data-dir data/ifeval
```

## 协议与结果

默认单并发、每题一次生成、自动重试为零，temperature=0、top_p=1、seed=42。输出上限分别为 GPQA/MATH-500 8192、GSM8K/IFEval/HumanEval 4096、C-Eval 2048，可用 `--max-tokens` 显式覆盖。默认发送通用字段，模型专有参数通过 `--extra-body config.json` 显式开启。启用思考输出时同时指定 `--thinking`，具体规则见协议。

最终回答只从 `message.content` 提取。无最终回答和截断事件保留在分母并单列。API 或评分基础设施异常会停止，报告标为未完成，指标暂为空。恢复时验证数据、配置和评分代码身份，已评分题跳过，已生成题仅补评分，请求结果未知的题停止并列为 `unknown`。

通用协议 v1 沿用历史评测中已核对的评分调用与恢复约束，并记录迁移差异。数据版本、few-shot 选择、模型专有参数及服务模板需结合实际报告解释。数值准确率由 EvalScope `Accuracy(numeric=True)` 固定实现决定。

详细资料见 [协议](docs/protocol.md)、[本地数据格式](docs/data.md)、[CPU 容器](docs/docker.md)、[来源与迁移差异](docs/provenance.md)、[验收记录](docs/validation.md)及[第三方许可](THIRD_PARTY_NOTICES.md)。

## 本地检查

```bash
python -m unittest discover -s tests -v
python scripts/check_release.py
```

发布文件通过代码、文档、合成示例白名单检查。`data/`、`outputs/`、虚拟环境、缓存和凭据保存在各自本地目录。
