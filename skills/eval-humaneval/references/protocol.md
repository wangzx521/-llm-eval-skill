# HumanEval 与 EvalPlus 协议

固定作者核心 commit 26d6d00bb1fd0fa37f39c99d5290da67891d1c5e 与官方 EvalPlus v0.1.10。每题只生成一次，候选分别进入 base/plus suite。expanded 指 plus suite。候选仅在受限 Linux CPU Docker 执行，缺少合格镜像时停止评分。详细镜像构建位于仓库 docs/docker.md。

指标为同一候选的 base 和 expanded pass@1。逐题生成持久化后再评分，缺失指标或非成功评分状态视为基础设施错误。完整计划分母保存在 prepared.json，未全部完成时 report.json 的 metrics 为空。

最终回答从 message.content 提取。闭合 think 标签后取其后的文本，未闭合标签或启用思考但输出未分离时记为无最终回答。reasoning/reasoning_content 仅作为分离状态依据。finish_reason=length 单列截断，已有最终回答仍正常评分。

通用数据格式、模型参数与容器说明随共享包仓库发布。读取完整协议时使用固定版本仓库的 docs/protocol.md。此 skill 的独立调用仅依赖已安装共享 Python 包。
