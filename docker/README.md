# docker —— 容器定义

初期基础设施：**Gitee + 本地 Docker**，云服务器后加。

owner：**C**（`team-mode/` 由 A 或 B 写一次即可）

> **状态（08-16）**：三个 Dockerfile 与冷启动验证器已落地；
> **但没有任何一个镜像被构建过** —— 写它们的环境里 Docker daemon 起不来。
> 下面「已落地 / 未验证」两栏严格分开，别把前者读成后者。

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

---

## 云服务器（后续）

现阶段不做。加入时新写一份 ADR，说明：为什么现在需要、部署什么、凭证怎么管、成本上限多少。

**别提前搭。** 现在搭 = 三个人分心去调 CI/CD，而地基还没打完。


---

## 已落地 / 未验证（08-16）

★ 这两栏**必须分开读**。本项目一路在拆的缺陷形态就是
「结构完整、从未被调用、而且没有任何测试会红」——
把「文件写好了」说成「能跑」，正是那种形态。

### 已落地且**真的跑过**

| 东西 | 怎么验的 |
|---|---|
| 冷启动验证器 `packages/delivery/codentum_delivery/cold_start/verify.py` | 11 条 pytest，其中真包由桌面端打包器经 vitest 现产 |
| 命令行打包入口 `packages/desktop/scripts/package-artifact.mts` | 本机 Node 真跑过，产出真实交付包 |
| **端到端**：TS 造包 → Python 验包 | 手工跑通，退出码 0 |
| 门禁 `scripts/check_docker.py` | 四条规则逐条构造违例验证会红（其中一条据此抓出是死规则） |
| 打包器排除构建缓存 | 因果检验：撤回排除表，测试报出真实的「交付包路径过长」 |

### 已写但**没验证过**

| 东西 | 为什么没验 | 谁来验 |
|---|---|---|
| `cold-start/Dockerfile` | Docker daemon 起不来，没 build 过 | 有 Docker 的人跑一次 |
| `delivery/Dockerfile` | 同上；且 `npm ci` 的行为只有真建才知道 | 同上 |
| `team-mode/Dockerfile` | 同上；NodeSource 源在离线环境会失败 | 同上 |
| 基础镜像 digest | 取 digest 要连 registry 真拉一次 | 见各 Dockerfile 顶部的补法 |

★ **digest 那一栏刻意留空而不是填一个** —— 编造的 digest 会让构建以一个
完全不指向真实原因的错误失败，而且它看起来像是验证过的。

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
docker run --rm -v /path/to/out:/delivery codentum-cold-start
```
