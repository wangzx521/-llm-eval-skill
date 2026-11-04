# 来源与迁移差异

2026-11-04 从已有项目 P4 runner、score、aggregate 与 vendor guards 中迁移通用控制流。历史实现的文件 SHA-256 另存 `source-migration.json`，供来源核对。历史响应、原题、GPU 信息和实验目录均由原工作区保存。

沿用的行为包括逐题串行持久化、最终回答提取、数值准确率调用、成功状态检查、IFEval 约束加权、C-Eval 微/宏平均及同一代码候选对应两套测试。

迁移时作出以下通用化选择。

- 模型名、API 地址、目录和代码镜像改为显式参数。认证仅经环境变量读取。
- 数据准备使用固定 revision 的 Hugging Face 数据和官方 EvalPlus release，也支持预渲染本地数据。
- few-shot 从已固定数据的 train/dev 前 N 条取样。历史冻结数据、模型 tokenizer、chat template 和缓存身份由原项目独立保存。
- 默认 temperature=0、top_p=1、seed=42，模型专有 thinking 参数需显式配置。请求使用标准库 HTTP 客户端，禁用自动重试和重定向。
- 评分进程使用通用 30/31 秒 POSIX CPU 上限，墙钟上限可记录配置。HumanEval 使用独立容器时限。
- 恢复通过本地 OS 文件锁和原子逐题记录实现，身份含数据、参数和评分环境。
- 新协议结果按其保存的配置解释。历史 Qwen 分数的精确复现还涉及原冻结数据、服务模板和专用环境。

EvalPlus v0.1.10 的作者参考程序曾在历史验收中出现 task 2 的两项 expanded 用例失败，保持原测试与容差，并在历史报告中披露。本工具保留作者测试语义，使用者应结合参考实现验收结果解释代码评分。
