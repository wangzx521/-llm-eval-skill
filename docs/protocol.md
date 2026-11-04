# 通用协议 v1

## 数据与提示词

下载准备通过 EvalScope 1.14.0 的 `record_to_sample`、`_post_process_samples` 渲染并保存全部消息和 Sample。GSM8K 使用固定版本 train 的前 4 条示例，C-Eval 使用各学科 dev 的前 5 条，其他任务为 0-shot。选择顺序、题目 ID、源提交、split、数据指纹及最终 JSONL SHA-256 均记录在数据清单。

GPQA 选项沿用固定适配器的按题目哈希确定顺序。C-Eval 使用 val。HumanEval 使用官方 EvalPlus v0.1.10 164 道题，以同一份 prompt 生成一次候选，两个测试套件分别计分。

## 生成

API 地址应包含 `/v1` 或服务实际基路径，程序追加 `/chat/completions`。仅从环境变量 `OPENAI_API_KEY` 读取认证。持久化精确请求体、响应中的 choices/usage/model/id、提取结果、评分及检查点。认证头、错误响应正文和服务自定义响应顶层字段不写入记录。

`--extra-body` 允许 `chat_template_kwargs`、`reasoning_effort`、`top_k`、`repetition_penalty`、`min_p`。`chat_template_kwargs` 仅支持布尔 `enable_thinking`。`enable_thinking=true` 还须指定 `--thinking`。参数是否受接口支持由服务响应确定，接口错误停止且保留未知状态。

最终回答提取沿用历史 P4 规则。有 `</think>` 时取其后文本，存在未闭合 `<think>` 时记为无最终回答。`--thinking` 下，content 既无闭合标记又无独立 reasoning 字段时记为未分离思考。reasoning/reasoning_content 始终不参与补答案。空 content 留在分母。`finish_reason=length` 单列截断，若仍有可识别最终回答则正常评分。

## 评分

- GPQA、C-Eval 读取成功评分的 `value.accuracy`，缺失指标作为基础设施错误。
- GSM8K、MATH-500 先使用对应适配器提取答案，再调用 EvalScope 1.14.0 `Accuracy(numeric=True)`。独立工作进程设墙钟上限，POSIX 另设 CPU 上限 30/31 秒。
- IFEval 读取四项完整指标，验证 instruction strict/loose 权重等于原题约束数。prompt 指标按题平均，instruction 指标按约束数加权。
- C-Eval 微平均为总正确数除以总题数，宏平均为所评学科正确率的算术平均。样本上限可能使报告仅包含部分学科。
- HumanEval 在受限 Linux CPU Docker 中调用锁定作者核心的 `trusted_exec` 和 `untrusted_check`。base/plus 各自执行一套测试。expanded 指标对应历史 expanded 口径，即 plus suite 的 pass@1。每题仅一个候选，样本总数保持一致。
- EvalPlus 内部默认最小用例时限 4 秒、参考耗时系数 4、suite 默认上限 60 秒。外层容器 145 秒运行闹钟和 155 秒墙钟，Python 评分工作进程默认 210 秒。基础设施超时停止，作者评分器正常返回的 candidate timeout 计为该套测试失败。

## 恢复与文件

每个输出目录有 OS 排他锁，进程退出后释放。逐题文件通过 fsync 和原子替换写入。发送前持久化 `inflight` 和请求体，收到响应先持久化 `generation` 再评分。崩溃后仍无响应的 inflight 转为 unknown，自动请求暂停。unknown 的处理需根据服务端证据由使用者单独判断。

数据清单、所选样本、参数、包版本、评分工作进程哈希、EvalScope 源文件哈希、相关依赖版本及 IFEval NLTK 数据哈希参与身份校验。配置变更需新建输出目录。生成或评分缺失时总体指标为空，报告保留完整计划分母和状态计数。

`score` 与 `report` 离线。评分进程阻止 Python socket 网络事件。候选程序的宿主机执行路径完全由 Docker 后端封装。
