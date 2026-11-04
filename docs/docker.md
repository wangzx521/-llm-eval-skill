# HumanEval CPU 容器

代码评分使用 Linux 主机和 Linux Docker Engine。先安装共享包，再显式构建镜像。镜像仅包含 Python、numpy、psutil 和逐文件哈希锁定的 EvalPlus 作者评分核心。

```bash
docker pull python:3.12-slim-bookworm
BASE_IMAGE=$(docker image inspect python:3.12-slim-bookworm --format '{{index .RepoDigests 0}}')
# RepoDigest 通常省略标签，组合为脚本接受的显式 Python 版本与 digest
BASE_DIGEST=${BASE_IMAGE#*@}
python scripts/build_code_image.py --base-image "python:3.12-slim-bookworm@$BASE_DIGEST" --output-dir code-image-build
CODE_IMAGE=$(cat code-image-build/image-id.txt)
llm-eval run --test humaneval --data-dir data/humaneval --model your-model --base-url http://127.0.0.1:8000/v1 --output-dir outputs/code --code-image "$CODE_IMAGE"
```

`code-image-build` 应创建于本地私有工作目录，公开发布只包含源码文件。数据通过标准输入传入，容器没有宿主机目录挂载、Docker socket 或 GPU 映射。

固定限制包括非 root UID/GID 65534、只读根文件系统、network=none、cap-drop=ALL、no-new-privileges、默认 seccomp、1 CPU、512 MiB 内存与相同 swap 上限、32 个 PID、文件描述符与文件大小上限、64 MiB noexec/nosuid/nodev 临时目录。

预检实际检查容器 UID、NoNewPrivs、CapEff、seccomp 状态、根目录写入失败、网络连接失败、cgroup 内存/PID/CPU 限制以及作者核心 SHA-256，预检通过后才允许模型请求。镜像 ID 和数据身份进入运行配置。候选程序只能到达该容器执行入口。

每个容器名称随机且唯一，外层超时仅停止本次创建的容器。作者内部测试时限与外层基础设施时限分开记录。环境验证状态见验收记录。
