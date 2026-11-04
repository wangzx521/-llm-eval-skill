# 第三方来源与许可

原创代码及历史项目中自行编写的编排逻辑由 Zhenxiang Wang 按仓库 MIT LICENSE 发布。

EvalScope 1.14.0 由 ModelScope/Alibaba 提供，采用 Apache License 2.0。本项目通过安装依赖调用其适配器、解析器和指标，相关权利与许可归上游。许可原文保存在 `licenses/EvalScope-Apache-2.0.txt`。来源见上游仓库。[EvalScope](https://github.com/modelscope/evalscope)

EvalPlus 作者评分核心锁定为 `26d6d00bb1fd0fa37f39c99d5290da67891d1c5e`，采用 Apache License 2.0，eval 子目录还遵循源自 OpenAI HumanEval 的 MIT 许可。原文保存在 `licenses/EvalPlus-LICENSE.txt` 与 `licenses/OpenAI-HumanEval-MIT.txt`。CPU 镜像构建脚本逐文件验证 SHA-256，保留上游 LICENSE。仅在用户显式构建镜像时下载核心文件。锁定清单见包内 `evalplus-lock.json`。[EvalPlus](https://github.com/evalplus/evalplus/tree/26d6d00bb1fd0fa37f39c99d5290da67891d1c5e)

GPQA、GSM8K、MATH-500、C-Eval、HumanEval/Plus 和 IFEval 数据的访问及使用条件由各数据发布方规定。使用 `prepare` 时获取所选数据。本仓库仅附自行编写的合成示例。
