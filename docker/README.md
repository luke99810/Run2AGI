# docker —— 容器定义

初期基础设施：**Gitee + 本地 Docker**，云服务器后加。

owner：**C**（`team-mode/` 由 A 或 B 写一次即可）

> **状态（08-16）**：三个镜像均已真实构建。delivery 容器造包后，
> cold-start 容器以非 root 用户完成校验、解包并执行 `codentum-start.sh`；
> team-mode 容器已连通宿主 Docker daemon，并完成真引擎握手。

---

## 三个目录

| 目录 | 用途 | 什么时候跑 |
|---|---|---|
| `cold-start/` | ★ 干净环境冷启动复现测试 | 每次交付前 |
| `team-mode/` | 多 Agent 并行的运行环境 | 开发中 |
| `delivery/` | 打包产物的构建环境 | 出包时 |

---

## cold-start/ 是最重要的那个

在一个**什么都没装**的容器里，只拿交付产物 + Provisioning 收集到的凭证，从零跑到能用。

它验的不是代码对不对，是**"能不能交付"**。绝大多数"在我机器上是好的"死在这里：

- 漏装的依赖
- 写死的本机路径
- 没记录的环境变量
- **手工执行过但没写进脚本的那一步** ← 最常见

**不许"先手工装一下再跑"。** 那样它什么都没验到。

---

## ⚠️ Windows 开发 + Linux 容器的坑

根目录的 `.gitattributes` 里有这一行：

```
*.sh text eol=lf
```

**不要删。**

没有它，Windows 上 checkout 出来的 `.sh` 会带 CRLF，在容器里报：

```
/bin/sh^M: bad interpreter: No such file or directory
```

这个报错**完全不指向真实原因**——你会去查解释器路径、查权限、查镜像，能耗掉半天。三个人都在 Windows 上开发，这个坑一定会踩，除非提前堵上。

---

## 硬约束

1. **容器里不烘焙凭证。** 凭证走运行时挂载或环境变量注入，不进镜像层——**镜像层删不掉，push 出去就是永久泄漏**。
2. **cold-start 必须从零，不许复用宿主机缓存。** 复用了就验不到"漏装的依赖"。
3. **镜像要 pin 版本**（不用 `latest`）。冷启动测试的意义在于可复现，`latest` 会让它今天绿明天红。
4. **每个 Dockerfile 顶部写一句"这个容器是干什么的"。**

## 云服务器（后续）

现阶段不做。加入时新写一份 ADR，说明：为什么现在需要、部署什么、凭证怎么管、成本上限多少。

**别提前搭。** 现在搭 = 三个人分心去调 CI/CD，而地基还没打完。


---

## 构建与运行验证（08-16）

★ 「定义已写」和「运行已验证」必须分开。本项目一路在拆的缺陷形态就是
「结构完整、从未被调用、而且没有任何测试会红」——
把「文件写好了」说成「能跑」，正是那种形态。

| 东西 | 真实结果 |
|---|---|
| `cold-start/Dockerfile` | build 通过；读取 delivery 容器生成的 `0644` 包，以 UID 1000 校验 2 个文件及 SHA-256，解包后执行 `codentum-start.sh`，输出 `CODENTUM_COLD_START_OK` |
| `delivery/Dockerfile` | build 通过；容器内打包器生成、隔离解包并复核清单，退出码 0 |
| `team-mode/Dockerfile` | build 通过；镜像内 Docker CLI 连接宿主 daemon `29.6.2`；以 `--worker-runtime team` 启动并完成真引擎握手 |
| 基础镜像 | Python、Node、Docker CLI 均固定 tag + RepoDigest，不再依赖可漂移的 tag |
| 构建上下文 | 递归排除宿主 `node_modules`、release、缓存和虚拟环境；delivery 从约 565 MB 降到约 24 KB，team-mode 降到约 1.5 MB |
| 自动门禁 | `tests/test_check_docker.py` 同时守四条硬约束、递归上下文隔离和 Team-mode 装配 |

这次真实构建抓出并修复了四处此前单测看不到的问题：非递归 `.dockerignore` 会把
Windows 二进制送进 Linux 镜像；交付包 `0600 root:root` 导致冷启动用户读不到；
TAR 抹掉 `codentum-start.sh` 的执行位；team-mode 镜像未选择 Team runtime 且没有
Docker CLI。这里记录的是镜像与装配验证，**不等于**外部 AgentTeams 控制器、Matrix
凭据和真实模型任务已经存在；缺少这些外部条件时仍必须 fail closed。

---

## 验证命令

```bash
python scripts/check_docker.py                                  # 四条硬约束
python -m pytest packages/delivery/tests/test_cold_start_verify.py -q   # 冷启动验证器

# 端到端（需要 Node）
node --experimental-strip-types packages/desktop/scripts/package-artifact.mts <项目目录> /tmp/d.tar.gz
python packages/delivery/codentum_delivery/cold_start/verify.py /tmp/d.tar.gz

# 镜像（需要 Docker，构建上下文是仓库根）
docker build -f docker/cold-start/Dockerfile -t codentum-cold-start .
docker build -f docker/delivery/Dockerfile -t codentum-delivery .
docker build -f docker/team-mode/Dockerfile -t codentum-team-mode .

docker run --rm -v /path/to/project:/project:ro -v /path/to/out:/out \
  codentum-delivery /project /out/delivery.tar.gz
docker run --rm -v /path/to/out:/delivery:ro \
  codentum-cold-start /delivery/delivery.tar.gz

# Docker Desktop 的 socket 是 root:root 0660；镜像内 teammode 用户已加入 root 组。
# 普通 Linux 主机若 socket GID 不同，额外传 --group-add "$(stat -c '%g' /var/run/docker.sock)"。
docker run --rm -i -e DASHSCOPE_API_KEY -e AGENTTEAMS_ADMIN_PASSWORD \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /path/to/project:/project codentum-team-mode
```
