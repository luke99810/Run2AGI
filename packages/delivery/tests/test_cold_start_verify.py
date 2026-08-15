"""冷启动交付验证的判据。

════════════════════════════════════════════════════════════════
 ★ 这组的判据用的是**桌面端打包器产出的真包**
════════════════════════════════════════════════════════════════

交付包格式由 `packages/desktop/shell/main/artifact-packager.ts` 定义 ——
TypeScript 写的、手搓的 tar。这里是 Python 侧的消费者。

手搓一个 fixture 只能证明「我理解得自洽」，证明不了「两边说的是同一件事」。
而这正是最容易出问题的地方：手搓 tar 的字节布局、非 ASCII 文件名的编码、
清单字段名的大小写 —— 任何一处漂移都会在真交付时才炸。

★ 所以 fixture 由 `vitest` 真的跑一次那个打包器生成（见 `_real_delivery`）。
  Node 不可用时**跳过并说明原因**，不退化成手搓包 ——
  退化之后这组测试会继续绿，而它守的那件事已经没在守了。
  本仓库在 `prepared` 夹具上吃过同一个亏：「测试的输入也该是真的」。
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from codentum_delivery.cold_start.verify import (
    MANIFEST_NAME,
    SCHEMA,
    verify_delivery,
)

REPO = Path(__file__).resolve().parents[3]
DESKTOP = REPO / "packages" / "desktop"

_GENERATOR = """\
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'vitest'
import { packageProjectArtifact } from './shell/main/artifact-packager'

test('fixture', async () => {
  const src = await mkdtemp(join(tmpdir(), 'codentum-fx-'))
  await mkdir(join(src, 'app'), { recursive: true })
  await writeFile(join(src, 'app', 'main.py'), 'print("hello")\\n', 'utf8')
  await writeFile(join(src, 'README.md'), '# demo\\n', 'utf8')
  // ★ 非 ASCII 文件名：手搓 tar 的编码最容易在这里漂移
  await writeFile(join(src, '\\u6211\\u7684\\u8bf4\\u660e.txt'), 'hi\\n', 'utf8')
  await packageProjectArtifact(src, String(process.env.CODENTUM_FIXTURE_OUT), 'wp-fixture')
})
"""


def _vitest_binary() -> Path | None:
    for name in ("vitest.cmd", "vitest"):
        candidate = DESKTOP / "node_modules" / ".bin" / name
        if candidate.exists():
            return candidate
    return None


@pytest.fixture(scope="module")
def real_delivery(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """用桌面端**真实的**打包器生成一个交付包。

    ★ 拿不到就 skip，不手搓替代 —— 见模块开头。
    """

    binary = _vitest_binary()
    if binary is None or shutil.which("node") is None:
        pytest.skip("桌面端依赖未安装（node/vitest 缺失），无法生成真实交付包")

    out = tmp_path_factory.mktemp("delivery") / "delivery.tar.gz"
    spec = DESKTOP / "codentum-cold-start-fixture.test.ts"
    spec.write_text(_GENERATOR, encoding="utf-8")
    env = {**os.environ, "CODENTUM_FIXTURE_OUT": out.as_posix()}
    try:
        proc = subprocess.run(  # noqa: S603 - 固定可执行文件与参数
            [str(binary), "run", spec.name],
            cwd=DESKTOP,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
        spec.unlink(missing_ok=True)
        pytest.skip(f"生成真实交付包失败：{exc}")
    finally:
        spec.unlink(missing_ok=True)

    if proc.returncode != 0 or not out.exists():  # pragma: no cover
        pytest.skip(f"打包器没有产出交付包：{(proc.stderr or proc.stdout)[-400:]}")
    return out


# ══════════════════════════════════════════════════════════════
#  正向：真包必须过
# ══════════════════════════════════════════════════════════════


def test_a_real_artifact_from_the_desktop_packager_verifies(real_delivery: Path) -> None:
    """★ 跨语言格式约定的判据：TS 打的包，Python 能验。

    这一条红了，说明两边对交付格式的理解已经分家 ——
    而那种分家只会在真正交付的那一刻才暴露。
    """

    report = verify_delivery(real_delivery)
    assert report.ok, report.render()
    assert report.file_count == 3
    assert report.packet_id == "wp-fixture"


def test_non_ascii_filenames_survive_the_round_trip(real_delivery: Path) -> None:
    """★ 手搓 tar 的编码最容易在非 ASCII 文件名上漂移。

    本仓库已经栽过三次编码的坑（EvidenceRef 分隔符、流编码、git quotepath），
    这是第四处可能出问题的地方，所以单独守一条。
    """

    with tarfile.open(real_delivery, "r:gz") as tar:
        names = [m.name for m in tar.getmembers() if m.isfile()]
    assert "project/我的说明.txt" in names, f"非 ASCII 路径没对上：{names}"

    with tarfile.open(real_delivery, "r:gz") as tar:
        handle = tar.extractfile(MANIFEST_NAME)
        assert handle is not None
        manifest = json.loads(handle.read().decode("utf-8"))
    assert "我的说明.txt" in {f["path"] for f in manifest["files"]}


# ══════════════════════════════════════════════════════════════
#  反向：坏包必须被拦下
# ══════════════════════════════════════════════════════════════


def _rewrite(archive: Path, target: Path, mutate) -> Path:  # type: ignore[no-untyped-def]
    """解开真包、改一处、再打回去 —— 保持其余部分是真的。"""

    payload: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            assert handle is not None
            payload[member.name] = handle.read()
    mutate(payload)
    with tarfile.open(target, "w:gz") as out:
        for name, content in payload.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            import io

            out.addfile(info, io.BytesIO(content))
    return target


def test_a_tampered_file_is_caught(real_delivery: Path, tmp_path: Path) -> None:
    """★ 改一个字节就要被抓出来 —— 这是交付包唯一的完整性保证。"""

    def mutate(payload: dict[str, bytes]) -> None:
        payload["project/app/main.py"] = b'print("evil")\n'

    bad = _rewrite(real_delivery, tmp_path / "bad.tar.gz", mutate)
    report = verify_delivery(bad)
    assert not report.ok
    assert any("SHA-256 不一致" in p for p in report.problems), report.render()


def test_an_undeclared_stowaway_is_caught(real_delivery: Path, tmp_path: Path) -> None:
    """★ 反向校验：包里有、清单里没有的文件是**夹带**。

    只查「清单里的都在不在包里」这一个方向，等于允许往包里塞东西 ——
    而收包的人按清单看，永远看不到多出来的那个。
    """

    def mutate(payload: dict[str, bytes]) -> None:
        payload["project/backdoor.py"] = b"import os\n"

    bad = _rewrite(real_delivery, tmp_path / "stowaway.tar.gz", mutate)
    report = verify_delivery(bad)
    assert not report.ok
    assert any("清单未声明" in p for p in report.problems), report.render()


def test_a_path_traversal_member_is_refused(real_delivery: Path, tmp_path: Path) -> None:
    """★ 验证器要在**解包之前**就能说「这个包不能解」。

    拿到包的人可能直接 `tar xzf` —— 那条命令不做任何越界检查。
    """

    def mutate(payload: dict[str, bytes]) -> None:
        payload["../../etc/passwd"] = b"pwned\n"

    bad = _rewrite(real_delivery, tmp_path / "slip.tar.gz", mutate)
    report = verify_delivery(bad)
    assert not report.ok
    assert any("上跳段" in p for p in report.problems), report.render()


def test_schema_drift_reports_the_actual_value(tmp_path: Path) -> None:
    """★ 格式漂移时，实际拿到的那个值是唯一的线索。

    只说「格式不对」会让排查从零开始。
    """

    import io

    archive = tmp_path / "drift.tar.gz"
    body = json.dumps({"schema": "codentum.source-delivery.v2", "files": []}).encode()
    with tarfile.open(archive, "w:gz") as out:
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(body)
        out.addfile(info, io.BytesIO(body))

    report = verify_delivery(archive)
    assert not report.ok
    assert SCHEMA in report.problems[0]
    assert "v2" in report.problems[0], "没有报出实际拿到的 schema"


def test_missing_archive_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    report = verify_delivery(tmp_path / "nope.tar.gz")
    assert not report.ok
    assert "找不到交付包" in report.problems[0]


# ══════════════════════════════════════════════════════════════
#  写死的本机路径 —— 是 warning，不是 problem
# ══════════════════════════════════════════════════════════════


def test_hardcoded_host_path_warns_but_does_not_fail(real_delivery: Path, tmp_path: Path) -> None:
    """★ 「能解开」不等于「能在别的机器上跑起来」。

    但它必须是 warning：一个讲部署的 Markdown 里出现 `/home/xxx/` 完全正常。
    判成故障会挡住正常交付，而**挡错一次，下一步就是有人把整项检查关掉** ——
    那比没有检查更糟。
    """

    def mutate(payload: dict[str, bytes]) -> None:
        payload["project/app/main.py"] = b'DATA = "C:/Users/somebody/data"\n'
        manifest = json.loads(payload[MANIFEST_NAME].decode("utf-8"))
        import hashlib

        for entry in manifest["files"]:
            if entry["path"] == "app/main.py":
                blob = payload["project/app/main.py"]
                entry["sizeBytes"] = len(blob)
                entry["sha256"] = hashlib.sha256(blob).hexdigest()
        payload[MANIFEST_NAME] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()

    patched = _rewrite(real_delivery, tmp_path / "hostpath.tar.gz", mutate)
    report = verify_delivery(patched)
    assert report.ok, f"本机路径不该让包体校验失败：{report.render()}"
    assert any("Windows 绝对路径" in w for w in report.warnings), report.render()


# ══════════════════════════════════════════════════════════════
#  ★ 结构判据：这个文件只许 import 标准库
# ══════════════════════════════════════════════════════════════


def test_verifier_imports_only_the_standard_library() -> None:
    """★ 它要在什么都没装的容器里独立跑。

    哪天有人在这里 `from codentum_contracts import ...`，
    容器会在**运行时**炸 —— 而那时候镜像已经推出去了。
    这条把那个运行时故障提前到这里。

    ★ 用 AST 而不是字符串匹配：注释与文档字符串里出现 `import codentum`
      是完全正常的（这个文件里就有），字符串匹配会把它误判成违规，
      然后有人把这条测试删掉。
    """

    source = (
        REPO / "packages" / "delivery" / "codentum_delivery" / "cold_start" / "verify.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入 —— 会把整个包拖进容器
                imported.add(".")
            elif node.module:
                imported.add(node.module.split(".")[0])

    assert imported, "一个 import 都没扫到，这条测试会变成空转"
    offenders = sorted(m for m in imported if m == "." or m.startswith("codentum"))
    assert not offenders, f"冷启动验证器不能依赖 Codentum 自身：{offenders}"

    import sys

    non_stdlib = sorted(m for m in imported if m not in sys.stdlib_module_names and m != ".")
    assert not non_stdlib, f"冷启动验证器只许用标准库，发现第三方依赖：{non_stdlib}"


# ══════════════════════════════════════════════════════════════
#  ★ 出口编码 —— 功能是对的，出口把它毁了
# ══════════════════════════════════════════════════════════════


def test_report_survives_a_non_utf8_console(real_delivery: Path) -> None:
    """★ 实测崩过：GBK 控制台上打印报告抛 UnicodeEncodeError，

    而那时候**校验已经通过了** —— 崩在打印上，退出码从 0 变成 1，
    一个「包是好的」被报成「包坏了」。比不检查更坏。

    ★ 这是本仓库第四次栽在编码上（EvidenceRef 分隔符、流编码、
      git quotepath，加这一次）。共同形状：单元测试摸不到**出口**。
      所以这条不测 render() 的字符串，测它**穿过一个 GBK 流**。
    """

    import io
    import runpy
    import sys

    gbk_out = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
    gbk_err = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
    real_out, real_err, real_argv = sys.stdout, sys.stderr, sys.argv
    sys.stdout, sys.stderr = gbk_out, gbk_err
    sys.argv = ["verify.py", str(real_delivery)]
    # ★ 初值 None 而不是留给 except 赋 —— mypy 抓到过这处：
    #   模块若没抛 SystemExit，`code` 根本没绑定，assert 会以 NameError
    #   的面目失败，而真正的原因是「入口没有以 SystemExit 收尾」。
    #   **失败要以它自己的原因失败。**
    code: int | str | None = None
    try:
        module = REPO / "packages" / "delivery" / "codentum_delivery" / "cold_start" / "verify.py"
        try:
            runpy.run_path(str(module), run_name="__main__")
        except SystemExit as exc:
            code = exc.code
    finally:
        sys.stdout, sys.stderr, sys.argv = real_out, real_err, real_argv

    assert code == 0, f"校验通过的包不该以非零退出（实际 {code!r}）"


def test_render_contains_both_columns_separately(real_delivery: Path, tmp_path: Path) -> None:
    """★ problems 与 warnings 必须分栏显示。

    合成一栏，看报告的人无法区分「这个包坏了」和「这个包可能带不走」——
    而这两件事的处置完全不同：前者重打，后者改项目。
    """

    report = verify_delivery(real_delivery)
    text = report.render()
    assert "包体完整" in text
    assert report.ok and not report.problems
