# GPQA Diamond 协议

GPQA Diamond 使用 0-shot。保留题目绑定的确定性选项顺序，以 EvalScope 1.14.0 选项提取与 value.accuracy 评分。数据访问条件由使用者满足。

指标为选项正确率。逐题生成持久化后再评分，缺失指标或非成功评分状态视为基础设施错误。完整计划分母保存在 prepared.json，未全部完成时 report.json 的 metrics 为空。

最终回答从 message.content 提取。闭合 think 标签后取其后的文本，未闭合标签或启用思考但输出未分离时记为无最终回答。reasoning/reasoning_content 仅作为分离状态依据。finish_reason=length 单列截断，已有最终回答仍正常评分。

通用数据格式、模型参数与容器说明随共享包仓库发布。读取完整协议时使用固定版本仓库的 docs/protocol.md。此 skill 的独立调用仅依赖已安装共享 Python 包。
