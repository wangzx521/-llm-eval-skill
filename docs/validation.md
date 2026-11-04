# 验收记录

本地验收日期为 2026-11-04，环境为 Windows、Python 3.12。

| 检查 | 结果 |
|---|---|
| 六个 skill 格式、链接、脚本入口 | 六项通过 skill-creator quick_validate |
| 核心单元与模拟 API 测试 | 22 项通过，含评分工作进程中断后的清理检查 |
| 正常回答、空 final、截断、HTTP 失败 | 已通过合成样例检查 |
| 评分超时、中断后恢复、未知请求停止 | 已通过，生成次数保持不变 |
| 配置、数据、评分环境变化拒绝恢复 | 已通过 |
| C-Eval 微/宏平均、IFEval 约束加权 | 已通过合成算例 |
| 同一代码候选绑定两套测试 | 已通过后端契约检查 |
| 共享包 wheel 构建 | 已完成 |
| 干净 venv 安装核心 wheel，单独复制一个 skill 到源码目录外调用 | 已完成 |
| 历史保存记录小样本回归 | 六组各 3 条，共 18 条最终回答提取与保存评分聚合通过 |
| 发布范围与凭据形状检查 | 已通过源码白名单检查 |
| 完整 EvalScope 评分依赖运行 | Linux GitHub Actions 中已通过七项合成上游集成测试 |
| Linux Docker 隔离与超时 | 已在 GitHub Actions 实际构建镜像，验证核心哈希、非 root、只读根目录、禁网、capabilities、seccomp、代码双套件与外层超时 |

干净环境安装验证使用标准 wheel 和 pip。此受管 Windows 环境的临时目录 ACL 导致常规 pip 构建失败，因此本地通过 setuptools wheel 命令构建，并在安装测试脚本内使用继承工作区权限的任务专用临时目录。包的正常安装命令保留标准 pip 用法。

2026-11-04 的首次 Linux 验收在提交 `58b623912feb29dd3ec5f53774d7069991a5cbac` 上完成，core 与 scoring 两个任务均成功。scoring 任务实际运行 28 项测试，全部通过。环境为 Python 3.12.15、EvalScope 1.14.0，容器使用 Python 3.12、numpy 1.26.4、psutil 7.2.2 及锁定 EvalPlus 核心。后续源码提交继续触发同一工作流。[首次验收记录](https://github.com/wangzx521/-llm-eval-skill/actions/runs/41839461591)

容器资格检查另读取 cgroup，验证 512 MiB 内存、32 PID 和 1 CPU 的实际限制。最新提交的结果可在工作流页面查看。[Linux 验收工作流](https://github.com/wangzx521/-llm-eval-skill/actions/workflows/validate.yml)

真实数据全量下载、GPQA 访问授权以及使用者模型 API 的适配由各自运行时核验。本轮验证使用合成输入和已有保存记录。

历史回归只读取现存生成与保存评分，评分器本身未重跑。历史原题、回答、数据和本地路径均未纳入公开文件。
