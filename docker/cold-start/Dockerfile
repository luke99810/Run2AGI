# 这个容器是干什么的：
#   在一个**什么都没装**的环境里，只拿一个交付包，判断它能不能交付。
#   它验的不是代码对不对，是「能不能交付」—— 见 docker/README.md。
#
# ★ 为什么基础镜像里只有 Python，没有 Codentum：
#   装了 Codentum 再去验交付包，验的就不是「从零」了。
#   验证器是单文件、零第三方依赖的（有测试守着这条约束：
#   packages/delivery/tests/test_cold_start_verify.py）。
#
# ★ 用具名 tag 而不是 latest（docker/README.md 硬约束第 3 条）。
#
# ★ tag 与 digest 同时固定；摘要来自真实 pull 后的 RepoDigest。
FROM python:3.11-slim@sha256:a630a63cdb314e2d138a2fca3e375e319e8568346ffafac5b980f888630ac4f1

# ★ 不装任何构建工具、不 apt-get update：
#   装了就等于给容器补上了交付包本该自带的东西，
#   而「漏装的依赖」正是冷启动要抓的头号问题。

WORKDIR /cold-start

# ★ 构建上下文是**仓库根**，不是本目录：
#     docker build -f docker/cold-start/Dockerfile -t codentum-cold-start .
#
#   这样 verify.py 只有一份（在 packages/delivery/ 里，受 mypy 与 pytest 管），
#   而不是在 docker/ 下再放一份副本。两份副本一定会漂移，
#   而漂移的那一刻不会有任何东西变红 —— 镜像里跑的是旧的那份。
#
# ★ 仍然只 COPY 这两个文件。COPY 整个仓库会让「从零」失效 ——
#   被验的项目会在容器里看到 Codentum 的源码。
COPY packages/delivery/codentum_delivery/cold_start/verify.py /cold-start/verify.py
COPY docker/cold-start/entrypoint.sh /cold-start/entrypoint.sh

# ★ 以非 root 运行：交付包是**外来输入**，用 root 解开它没有任何理由。
RUN chmod 0755 /cold-start/entrypoint.sh \
 && useradd --create-home --shell /usr/sbin/nologin coldstart \
 && mkdir -p /delivery /workspace \
 && chown coldstart:coldstart /delivery /workspace
USER coldstart

# ★ 凭证一律不进镜像层 —— 镜像层删不掉，push 出去就是永久泄漏。
#   需要凭证时用 `docker run --env-file` 或 `-e` 在运行时注入。
#   这里刻意不写任何 ENV 占位，免得有人往里填。

VOLUME ["/delivery"]

ENTRYPOINT ["/cold-start/entrypoint.sh"]
CMD ["/delivery/delivery.tar.gz"]
