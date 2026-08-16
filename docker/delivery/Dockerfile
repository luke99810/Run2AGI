# 这个容器是干什么的：
#   产出交付包 —— 跑桌面端的打包器（artifact-packager），把一个项目目录
#   打成 `delivery.tar.gz`（含 CODENTUM-DELIVERY.json 与逐文件 SHA-256）。
#
# ★ 为什么打包要进容器：打包器会遍历项目目录并计算摘要，
#   而宿主机上残留的构建缓存、虚拟环境、编辑器临时文件都会影响结果。
#   容器里跑一次，产出才是可复现的。
#
# ★ 与 cold-start 的分工：这里**造包**，那里**验包**。
#   两个镜像刻意不合并 —— 合并之后「验」就能看到「造」的中间产物，
#   而冷启动的全部意义在于它只拿得到交付包本身。
#
# ★ tag 与 digest 同时固定；摘要来自真实 pull 后的 RepoDigest。
#   node:22 起支持 `--experimental-strip-types`，下面的 .mts 入口靠它直接跑，
#   不引入 ts 编译步骤。
FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

WORKDIR /build

# ★ 只装打包器需要的依赖，不装 electron —— 打包器是纯 Node 代码，
#   而 electron 的下载有几百 MB 且需要网络，装了会让这个镜像
#   在离线环境下建不出来。
COPY packages/desktop/package.json packages/desktop/package-lock.json* /build/
RUN npm ci --omit=optional --ignore-scripts

COPY packages/desktop /build/

# ★ 凭证不进镜像层 —— 镜像层删不掉，push 出去就是永久泄漏。
#   打包本身不需要任何凭证；如果哪天需要了，用运行时注入。

VOLUME ["/project", "/out"]

# 用法：
#   docker run --rm -v /path/to/project:/project:ro -v /path/to/out:/out \
#     codentum-delivery /project /out/delivery.tar.gz
ENTRYPOINT ["node", "--experimental-strip-types", "/build/scripts/package-artifact.mts"]
