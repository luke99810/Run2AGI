"""只解析 boundaries.yaml 用到的那部分 YAML。

★ 为什么不引 PyYAML：
  check_boundaries 要能在 `pip install` 之前跑 —— 它检查的是团队边界，
  而边界最容易在"还没来得及装依赖"的那种时刻被破坏。

★ 为什么这样做是安全的：
  遇到不认识的语法【直接抛错】，不猜、不跳过。
  一个静默猜错的 YAML 解析器比没有解析器危险得多 ——
  它会让 check_boundaries 报告一个与文件内容无关的结论。

支持：映射 / 序列 / 序列中的映射 / 行内数组 / 折叠块标量(>) /
     注释 / null / 数字 / 布尔 / 带引号字符串
不支持（抛错）：锚点(&*) / 多文档(---) / 流式映射({}) / 竖线块标量(|)
"""

from __future__ import annotations

import json
import re
from typing import Any

_KEY_RE = re.compile(r"^([\w.$-]+):(.*)$")


class YamlLiteError(ValueError):
    pass


def parse_yaml(text: str) -> Any:
    lines: list[dict[str, Any]] = []
    for i, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if re.match(r"^---\s*$", raw):
            raise YamlLiteError(f"第 {i} 行：yaml-lite 不支持多文档分隔符 ---")
        if re.search(r"(^|\s)[&*][A-Za-z]", raw):
            raise YamlLiteError(f"第 {i} 行：yaml-lite 不支持锚点/别名")
        lines.append({"indent": len(raw) - len(raw.lstrip(" ")), "text": raw.strip(), "no": i})

    if not lines:
        return None
    value, nxt = _parse_block(lines, 0, lines[0]["indent"])
    if nxt != len(lines):
        raise YamlLiteError(f"第 {lines[nxt]['no']} 行：缩进未闭合，解析中止")
    return value


def _parse_block(lines: list[dict[str, Any]], i: int, indent: int) -> tuple[Any, int]:
    if i >= len(lines):
        return None, i
    if lines[i]["text"].startswith("- ") or lines[i]["text"] == "-":
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_seq(lines: list[dict[str, Any]], i: int, indent: int) -> tuple[list[Any], int]:
    out: list[Any] = []
    while i < len(lines) and lines[i]["indent"] == indent and (
        lines[i]["text"].startswith("- ") or lines[i]["text"] == "-"
    ):
        rest = lines[i]["text"][1:].strip()
        if rest == "":
            child_indent = lines[i + 1]["indent"] if i + 1 < len(lines) else indent + 2
            v, i = _parse_block(lines, i + 1, child_indent)
            out.append(v)
        elif _KEY_RE.match(rest):
            item_indent = lines[i]["indent"] + 2
            synthetic = [{"indent": item_indent, "text": rest, "no": lines[i]["no"]}]
            j = i + 1
            tail = []
            while j < len(lines) and lines[j]["indent"] >= item_indent:
                tail.append(lines[j])
                j += 1
            v, consumed = _parse_map([*synthetic, *tail], 0, item_indent)
            if consumed != len(synthetic) + len(tail):
                raise YamlLiteError(f"第 {lines[i]['no']} 行附近：序列项解析未完整消费")
            out.append(v)
            i = j
        else:
            # ★ 序列项的行尾注释也要剥掉 —— 漏了这一步，
            #   `- "a/**"   # 说明` 会被当成一条含注释文字的路径，
            #   于是路径匹配永远不命中，而检查照样"通过"。
            out.append(_scalar(_strip_comment(rest).strip(), lines[i]["no"]))
            i += 1
    return out, i


def _parse_map(lines: list[dict[str, Any]], i: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while i < len(lines) and lines[i]["indent"] == indent:
        m = _KEY_RE.match(lines[i]["text"])
        if not m:
            raise YamlLiteError(f"第 {lines[i]['no']} 行：yaml-lite 看不懂 → {lines[i]['text']}")
        key, rest = m.group(1), _strip_comment(m.group(2)).strip()

        if rest.startswith("|"):
            raise YamlLiteError(f"第 {lines[i]['no']} 行：yaml-lite 不支持竖线块标量，请用 >")
        if rest in (">", ">-"):
            parts, j = [], i + 1
            while j < len(lines) and lines[j]["indent"] > indent:
                parts.append(lines[j]["text"])
                j += 1
            out[key] = " ".join(parts).strip()
            i = j
        elif rest == "":
            child_indent = lines[i + 1]["indent"] if i + 1 < len(lines) else None
            if child_indent is None or child_indent <= indent:
                out[key] = None
                i += 1
            else:
                out[key], i = _parse_block(lines, i + 1, child_indent)
        else:
            out[key] = _scalar(rest, lines[i]["no"])
            i += 1
    return out, i


def _strip_comment(s: str) -> str:
    """去掉行尾注释，但保留引号内的 #。"""
    in_s = in_d = False
    for k, ch in enumerate(s):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d and (k == 0 or s[k - 1].isspace()):
            return s[:k]
    return s


def _scalar(s: str, no: int) -> Any:
    if s.startswith("{"):
        raise YamlLiteError(f"第 {no} 行：yaml-lite 不支持流式映射 {{}}")
    if s.startswith("["):
        try:
            return json.loads(s.replace("'", '"'))
        except json.JSONDecodeError as e:
            raise YamlLiteError(f"第 {no} 行：行内数组不是合法 JSON → {s}") from e
    if s in ("null", "~"):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d*\.\d+", s):
        return float(s)
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s
