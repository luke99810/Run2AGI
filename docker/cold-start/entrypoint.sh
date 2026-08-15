#!/bin/sh
# 冷启动入口：验包 → 解包 → 交给项目自己的启动步骤。
#
# ★ 用 /bin/sh 不用 bash：基础镜像里没有 bash，而为了这个脚本去装 bash
#   等于往「什么都没装」的环境里加东西 —— 那正是要避免的。
#
# ★ 这个文件必须是 LF 换行。根目录 .gitattributes 里的 `*.sh text eol=lf`
#   就是为它写的；没有它，Windows 上 checkout 出来的脚本在容器里报
#   `/bin/sh^M: bad interpreter`，而那个报错完全不指向真实原因。
set -eu

ARCHIVE="${1:-/delivery/delivery.tar.gz}"

echo "── 第 1 步：校验交付包完整性 ───────────────────────────"
# ★ 先验后解。拿到包的人可能直接 tar xzf，而那条命令不做越界检查 ——
#   验证器要能在解包之前就说「这个包不能解」。
python /cold-start/verify.py "$ARCHIVE"

echo
echo "── 第 2 步：解包到干净目录 ─────────────────────────────"
mkdir -p /workspace/project
tar -xzf "$ARCHIVE" -C /workspace
echo "已解到 /workspace/project"

echo
echo "── 第 3 步：从零启动 ───────────────────────────────────"
cd /workspace/project

# ★ 下面这段是**故意不写死**的。
#
#   「从零跑到能用」的具体步骤属于**被交付的那个项目**，不属于这个容器。
#   在这里硬编码 `pip install -r requirements.txt && python main.py`
#   会让冷启动对着一个假想的项目形态跑 —— 而绝大多数交付物不长那样。
#
#   更要紧的是：硬编码会让「这一步本来就没写进脚本」变得**看不见**，
#   而那恰恰是 docker/README.md 列的最常见死因。
#
#   所以规则是：项目自带 codentum-start.sh 就跑它；没有就如实报「不知道怎么启动」
#   并以非零退出 —— **不许在这里替它猜，也不许静默算过**。
if [ -x ./codentum-start.sh ]; then
  echo "发现 ./codentum-start.sh，执行："
  exec ./codentum-start.sh
fi

if [ -f ./codentum-start.sh ]; then
  echo "✗ 存在 ./codentum-start.sh 但没有可执行位。" >&2
  echo "  在 Windows 上打包极易发生 —— git 的 core.filemode 不跟踪权限。" >&2
  exit 1
fi

echo "✗ 交付包里没有 ./codentum-start.sh，不知道如何从零启动这个项目。" >&2
echo >&2
echo "  这不是容器的问题，是**交付物缺了启动契约**：" >&2
echo "  「手工执行过但没写进脚本的那一步」正是冷启动要抓的东西。" >&2
echo "  修法：让交付物带一个 codentum-start.sh，把从零到能用的每一步写进去。" >&2
exit 1
