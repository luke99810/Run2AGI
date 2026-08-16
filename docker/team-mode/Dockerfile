# 这个容器是干什么的：
#   多 Agent 并行开发的运行环境 —— 跑 Codentum 引擎本身，
#   而不是跑它交付出来的东西（那是 cold-start 的事）。
#
# ★ 为什么要 Node：MCP server 靠 `npx` 拉起（见 packages/roles/mcp/）。
#   没有 Node 时引擎照常能跑，只是接不上任何第三方工具 ——
#   而那种「少了一半能力但看起来正常」正是本项目一直在拆的形态。
#
# ★ TeamWorkerRuntime 通过 `docker exec agentteams-controller agt ...` 调用
#   AgentTeams，所以只装 Python/Node 仍是假 Team-mode。Docker CLI 从官方
#   CLI 镜像复制；运行时再只读挂载宿主的 Docker socket。
FROM docker:29.0.0-cli@sha256:87858a6dd14149240f4a2c33e0ec0f32a5ac1bb2548c04f5a20694ea25cb6157 AS docker_cli

# ★ tag 与 digest 同时固定；摘要来自真实 pull 后的 RepoDigest。
FROM python:3.11-slim@sha256:a630a63cdb314e2d138a2fca3e375e319e8568346ffafac5b980f888630ac4f1

# ★ 用 --no-install-recommends 且装完就清 apt 缓存：
#   镜像层删不掉，装了再删只会让镜像更大，不会更小。
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# 放在重依赖层之后：升级 Docker CLI 不应让 apt/Node 层全部失去缓存。
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /codentum

# ★ 先只 COPY 依赖声明再装第三方依赖，最后才 COPY 源码 ——
#   源码一改就重装依赖会让每次迭代多等几分钟。
#
# ★ 这里刻意分成两步，而不是 COPY 完源码直接 `pip install -e .`：
#   `-e` 需要源码在场，一并做的话每改一行源码都要重解一次依赖树。
COPY pyproject.toml /codentum/
RUN pip install --no-cache-dir \
      "pydantic>=2.9" "langgraph>=0.2" "openai>=1.0" "anthropic>=0.40"

COPY packages /codentum/packages

# ★ `pip install -e .` 在 2026-08-16 之前是**建不过**的：
#   pyproject 里那份 `packages = [...]` 名单没有 package-dir，
#   setuptools 会去仓库根找 ./codentum_control_plane/ 而那里没有，
#   报 `package directory 'codentum_control_plane' does not exist`。
#
#   它一直没被发现，是因为**从来没有人装过这个包** —— 本地靠 conftest.py
#   把六个根挂到 sys.path 就能跑测试。「装不上」这件事在仓库内部
#   完全观测不到，直到要把它装进容器。已随本次改动修掉（packages.find）。
RUN pip install --no-cache-dir --no-deps -e .

# ★ 凭证一律不进镜像层 —— 镜像层删不掉，push 出去就是永久泄漏。
#   模型 Key 在运行时注入：
#     docker run --rm -i -e DASHSCOPE_API_KEY -e AGENTTEAMS_ADMIN_PASSWORD \
#       -v /var/run/docker.sock:/var/run/docker.sock \
#       -v /path/to/project:/project codentum-team
#   这里刻意不写 ENV 占位，免得有人往里填。

# ★ 以非 root 运行：引擎会执行模型产出的代码，那是**不可信输入**。
RUN useradd --create-home teammode \
 && usermod --append --groups root teammode \
 && git config --system --add safe.directory /project \
 && chown -R teammode:teammode /codentum
USER teammode

VOLUME ["/project"]

ENTRYPOINT ["python", "-m", "codentum_engine"]
CMD ["--project-root", "/project", "--worker-runtime", "team"]
