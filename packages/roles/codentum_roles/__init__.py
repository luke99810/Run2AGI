# ruff: noqa: RUF002
"""codentum_roles —— 角色体系

★ 一个角色 = 一组写权限 + 一组可见上下文 + 一组可触发的状态转换。
  提示词只是让它高效地用好这组权限。

  所以 ../specs/ 是角色的真身，../prompts/ 只是让它跑得顺。
  【不要反过来】—— 不要靠写提示词去实现约束。
  判据：这条约束如果 Agent 不配合会怎样？会失效 → 它不该在提示词里。

★ RoleSpec 是 Single Source，派生四处：
    工具面(harness) · 卷挂载(harness) · 状态转换表(control-plane) · 所有权注册(control-plane)
  任一处手工维护都会漂移，且漂移时通常不报错 —— 只是权限悄悄变宽。

★ 三层制衡：
    权限隔离    各角色 ownsPaths 不相交
    上下文隔离   Reviewer 读不到 Coder 的推理链（存储层强制）
    模型隔离    coder ≠ reviewer；evolver ≠ verifier（ModelGateway.open() 拒绝开会话）

★ 三种作弊结构性堵死，全靠挂载权限，不靠提示词：
    改测试   → tests/ 只读挂载
    改契约   → contracts/ 只读挂载
    缩小范围 → 验收谓词由 QA 先写，Coder 看不见也改不了

⚠️ 一条 schema 保证不了的约束：id == "guardian" 时 usesModel 必须为 False。
   JSON Schema 表达不了条件约束，由本包加载 RoleSpec 时强制。

owner: B ｜ 评审: A ｜ 详见 ../README.md 与 docs/01-角色详细设计与Skill清单.md
"""

from .loader import (
    RolePromptLoadError,
    RoleSkillLoadError,
    RoleSpecLoadError,
    default_prompts_dir,
    default_skills_dir,
    default_specs_dir,
    load_builtin_role_specs,
    load_role_prompt,
    load_role_skill_prompt,
    load_role_spec_file,
    load_role_specs_dir,
)

__all__ = [
    "RolePromptLoadError",
    "RoleSkillLoadError",
    "RoleSpecLoadError",
    "default_prompts_dir",
    "default_skills_dir",
    "default_specs_dir",
    "load_builtin_role_specs",
    "load_role_prompt",
    "load_role_skill_prompt",
    "load_role_spec_file",
    "load_role_specs_dir",
]
