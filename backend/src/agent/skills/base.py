# -*- coding: utf-8 -*-
"""交易技能（Skill）基类与 SkillManager。

技能以自然语言形式定义在 YAML 文件中，每条对应一种常见或自定义的交易模式
（例如：龙头策略、缩量回踩、均线金叉），用于股票分析与推送通知的判断输入。
最终用户无需写 Python 代码即可通过新增 YAML 文件扩展自身可用的技能集；
兼容性方面，内置 YAML 文件仍保留在 ``strategies/`` 目录下。

主要导出：
- :class:`Skill` 单条技能的 dataclass 表示
- :class:`SkillManager` 技能注册/激活/组合指令生成器
- :func:`load_skill_from_yaml` / :func:`load_skill_from_markdown` 单文件加载
- :func:`load_skills_from_directory` 目录批量加载
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# 内置技能 YAML 目录位于 backend/strategies；通过相对路径回溯到仓库的 strategies/。
_BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "strategies"


@dataclass
class Skill:
    """可注入到 Agent 提示词的交易技能条目。

    每条技能代表一种常见或自定义的交易模式，用于个股分析与推送通知；
    通常通过自然语言描述的 YAML 文件加载得到。

    Attributes:
        name: 唯一策略标识（如 ``"dragon_head"``）。
        display_name: 面向用户的展示名（如 ``"龙头策略"``）。
        description: 简要说明该策略的适用场景。
        instructions: 详细自然语言描述，会拼接到 system prompt 中。
        category: 技能类别——``"trend"``（趋势）、``"pattern"``（形态）、
            ``"reversal"``（反转）、``"framework"``（框架）。
        core_rules: 与本策略关联的核心交易理念编号（1-7）。
        required_tools: 本策略依赖的工具名列表。
        allowed_tools: 来自 SKILL.md frontmatter 的可选工具白名单元数据。
        aliases: 用于 NL 选择器 / 机器人命令的别名短语。
        enabled: 当前是否处于激活状态。
        source: 技能来源——``"builtin"`` 或自定义定义文件的绝对路径。
        entrypoint: 定义文件路径（YAML 或 SKILL.md）。
        bundle_dir: 从 SKILL.md 加载时所处的技能 bundle 目录。
        disable_model_invocation: 是否禁止模型自动调用此技能。
        user_invocable: 是否在面向用户的选择器中暴露此技能。
        default_active: 是否纳入默认激活集合。
        default_router: 是否纳入路由兜底选择集合。
        default_priority: 默认排序提示（值越小越靠前）。
        market_regimes: 技能路由器使用的可选市场状态标签。
        execution_context: 来自 frontmatter 的 inline/fork 执行提示。
        subagent_type: 来自 frontmatter 的可选子代理类型提示。
        preferred_model: 来自 frontmatter 的可选模型提示。
    """
    name: str
    display_name: str
    description: str
    instructions: str
    category: str = "trend"
    core_rules: List[int] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    enabled: bool = False
    source: str = "builtin"
    entrypoint: str = ""
    bundle_dir: str = ""
    disable_model_invocation: bool = False
    user_invocable: bool = True
    default_active: bool = False
    default_router: bool = False
    default_priority: int = 100
    market_regimes: List[str] = field(default_factory=list)
    execution_context: str = "inline"
    subagent_type: str = ""
    preferred_model: str = ""


# 匹配 SKILL.md 顶部 YAML frontmatter；DOTALL 用于跨行匹配到结束分隔符。
_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", re.DOTALL)


def _coerce_string_list(value: object) -> List[str]:
    """把 YAML/frontmatter 中的标量或列表值规整成干净的字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _coerce_bool(value: object, default: bool = False) -> bool:
    """把 YAML/字符串形式的常见布尔写法规整为 ``bool``。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _coerce_int(value: object, default: int = 100) -> int:
    """把 priority 类数值规整为 ``int``，解析失败时回退到安全默认值。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_skill_frontmatter(raw_text: str) -> tuple[Dict[str, object], str]:
    """把 SKILL.md 文件拆成 YAML frontmatter 元数据与 Markdown 正文两部分。

    若文件无 frontmatter，则返回空字典与去首尾空白后的原文。
    """
    import yaml

    match = _FRONTMATTER_RE.match(raw_text)
    if not match:
        return {}, raw_text.strip()

    metadata_raw, body = match.groups()
    metadata = yaml.safe_load(metadata_raw) or {}
    if not isinstance(metadata, dict):
        raise ValueError("Skill frontmatter must be a YAML mapping")
    return metadata, body.strip()


def _infer_skill_description(instructions: str) -> str:
    """当 frontmatter 没写 description 时，用 instructions 首段作为兜底描述。"""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", instructions or "") if part.strip()]
    if not paragraphs:
        return ""
    first = re.sub(r"\s+", " ", paragraphs[0]).strip()
    # 截断到 280 字符以内，避免 description 过长污染后续提示词。
    return first[:280]


def load_skill_from_yaml(filepath: Union[str, Path]) -> Skill:
    """从单个 YAML 文件加载一个 :class:`Skill`。

    YAML 必须至少包含 ``name``、``display_name``、``description``、
    ``instructions`` 四个字段，所有值均为自然语言文本。

    Args:
        filepath: ``.yaml`` / ``.yml`` 文件的路径。

    Returns:
        一个 ``enabled=False`` 的 :class:`Skill` 实例。

    Raises:
        ValueError: 必填字段缺失或文件内容不合法时抛出。
        FileNotFoundError: 文件不存在时抛出。
    """
    # 延迟导入：仅在加载技能时才需要 yaml，避免模块导入时的强制依赖。
    import yaml

    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Skill file not found: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid skill file (expected YAML mapping): {filepath}")

    # 校验必填字段，缺失时立即抛错，避免后续访问 data[...] 触发 KeyError。
    required_fields = ["name", "display_name", "description", "instructions"]
    missing = [fld for fld in required_fields if not data.get(fld)]
    if missing:
        raise ValueError(
            f"Skill file {filepath.name} missing required fields: {missing}"
        )

    return Skill(
        name=str(data["name"]).strip(),
        display_name=str(data["display_name"]).strip(),
        description=str(data["description"]).strip(),
        instructions=str(data["instructions"]).strip(),
        category=str(data.get("category", "trend")).strip(),
        core_rules=data.get("core_rules", []) or [],
        required_tools=data.get("required_tools", []) or [],
        allowed_tools=_coerce_string_list(data.get("allowed_tools")),
        aliases=_coerce_string_list(data.get("aliases")),
        enabled=False,
        source=str(filepath),
        entrypoint=str(filepath),
        bundle_dir=str(filepath.parent),
        disable_model_invocation=bool(data.get("disable_model_invocation", False)),
        user_invocable=bool(data.get("user_invocable", True)),
        default_active=_coerce_bool(data.get("default_active"), False),
        default_router=_coerce_bool(data.get("default_router"), False),
        default_priority=_coerce_int(data.get("default_priority"), 100),
        market_regimes=(
            # 兼容 kebab-case 与 snake_case 两种 key 命名。
            _coerce_string_list(data.get("market_regimes"))
            or _coerce_string_list(data.get("market-regimes"))
        ),
        execution_context=str(data.get("context", "inline")).strip() or "inline",
        subagent_type=str(data.get("agent", "")).strip(),
        preferred_model=str(data.get("model", "")).strip(),
    )


def load_skill_from_markdown(filepath: Union[str, Path]) -> Skill:
    """从 ``SKILL.md`` bundle 入口加载一条技能。

    文件由 YAML frontmatter（描述元信息）+ Markdown 正文（实际策略说明）组成；
    frontmatter 缺字段时会从文件名/正文首段做兜底推断。
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Skill file not found: {filepath}")

    raw_text = filepath.read_text(encoding="utf-8")
    metadata, instructions = _parse_skill_frontmatter(raw_text)
    if not instructions:
        raise ValueError(f"Skill file {filepath.name} missing markdown instructions")

    skill_name = str(metadata.get("name") or filepath.parent.name).strip()
    display_name = str(
        metadata.get("display_name")
        or metadata.get("title")
        or skill_name
    ).strip()
    description = str(
        # 缺 description 时用正文首段兜底，避免 SKILL.md 写得太简略导致空描述。
        metadata.get("description")
        or _infer_skill_description(instructions)
    ).strip()
    if not skill_name or not description:
        raise ValueError(f"Skill file {filepath.name} missing required name/description")

    # 同时支持 kebab-case 与 snake_case 两种 key 的兼容写法。
    allowed_tools = _coerce_string_list(metadata.get("allowed-tools"))
    if not allowed_tools:
        allowed_tools = _coerce_string_list(metadata.get("allowed_tools"))
    required_tools = _coerce_string_list(metadata.get("required-tools"))
    if not required_tools:
        required_tools = _coerce_string_list(metadata.get("required_tools"))

    return Skill(
        name=skill_name,
        display_name=display_name,
        description=description,
        instructions=instructions,
        category=str(metadata.get("category", "general")).strip() or "general",
        core_rules=metadata.get("core_rules", []) or [],
        required_tools=required_tools,
        allowed_tools=allowed_tools,
        aliases=_coerce_string_list(metadata.get("aliases")),
        enabled=False,
        source=str(filepath),
        entrypoint=str(filepath),
        bundle_dir=str(filepath.parent),
        disable_model_invocation=_coerce_bool(metadata.get("disable-model-invocation"), False),
        user_invocable=_coerce_bool(metadata.get("user-invocable"), True),
        default_active=_coerce_bool(
            metadata.get("default-active", metadata.get("default_active")),
            False,
        ),
        default_router=_coerce_bool(
            metadata.get("default-router", metadata.get("default_router")),
            False,
        ),
        default_priority=_coerce_int(
            metadata.get("default-priority", metadata.get("default_priority")),
            100,
        ),
        market_regimes=(
            _coerce_string_list(metadata.get("market-regimes"))
            or _coerce_string_list(metadata.get("market_regimes"))
        ),
        execution_context=str(metadata.get("context", "inline")).strip() or "inline",
        subagent_type=str(metadata.get("agent", "")).strip(),
        preferred_model=str(metadata.get("model", "")).strip(),
    )


def load_skills_from_directory(directory: Union[str, Path]) -> List[Skill]:
    """从一个目录下加载全部技能文件。

    扫描顶层 ``*.yaml`` / ``*.yml`` 兼容文件，以及任意深度的 ``SKILL.md`` bundle，
    按文件名字母序排序；解析失败的文件会被跳过并打 warning 日志。

    Args:
        directory: 含技能定义文件的目录路径。

    Returns:
        :class:`Skill` 实例列表（默认全部 ``enabled=False``）。
    """
    directory = Path(directory)
    if not directory.is_dir():
        logger.warning(f"Skill directory does not exist: {directory}")
        return []

    skills: List[Skill] = []
    yaml_files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    markdown_files = sorted(directory.rglob("SKILL.md"))

    # 先加载顶层 YAML 兼容文件，再递归加载 SKILL.md bundle；失败仅记日志、不影响整体。
    for filepath in yaml_files:
        try:
            skill = load_skill_from_yaml(filepath)
            skills.append(skill)
            logger.debug(f"Loaded skill from YAML: {skill.name} ({filepath.name})")
        except Exception as e:
            logger.warning(f"Failed to load skill from {filepath.name}: {e}")

    for filepath in markdown_files:
        try:
            skill = load_skill_from_markdown(filepath)
            skills.append(skill)
            logger.debug(f"Loaded skill bundle: {skill.name} ({filepath})")
        except Exception as e:
            logger.warning(f"Failed to load skill bundle from {filepath}: {e}")

    return skills


class SkillManager:
    """交易技能管理器：注册、激活并产出可拼接到提示词的组合指令。

    支持三种技能来源：
    1. 内置 ``strategies/`` 目录下的 YAML 文件；
    2. 用户自定义目录下的 YAML / SKILL.md 文件；
    3. 直接以 :class:`Skill` 实例程序化注册（向后兼容）。

    使用示例::

        manager = SkillManager()
        # 加载内置 + 自定义技能
        manager.load_builtin_skills()
        manager.load_custom_skills("./my_skills")
        # 也可以程序化注册
        manager.register(some_skill)
        # 激活并生成提示词片段
        manager.activate(["dragon_head", "shrink_pullback"])
        instructions = manager.get_skill_instructions()
    """

    def __init__(self):
        """创建一个内存中的空技能注册表。"""
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """注册一条技能（无论是程序化构造还是从 YAML 加载得到）。"""
        self._skills[skill.name] = skill
        logger.debug(f"Registered skill: {skill.name} ({skill.display_name})")

    def load_builtin_skills(self) -> int:
        """从兼容性 ``strategies/`` 目录加载全部内置技能。

        Returns:
            实际加载到的技能数量。
        """
        skills_dir = _BUILTIN_SKILLS_DIR
        if not skills_dir.is_dir():
            logger.warning(f"Built-in skill directory not found: {skills_dir}")
            return 0

        skills = load_skills_from_directory(skills_dir)
        for skill in skills:
            # 来自该目录的技能统一标记为 builtin，便于在元信息中区分来源。
            skill.source = "builtin"
            self.register(skill)

        logger.info(f"Loaded {len(skills)} built-in skills from {skills_dir}")
        return len(skills)

    def load_custom_skills(self, directory: Union[str, Path, None]) -> int:
        """从用户自定义目录加载技能；同名时自定义技能覆盖内置。

        Args:
            directory: 自定义技能目录路径。若为 ``None`` 或为空则跳过。

        Returns:
            实际加载到的技能数量。
        """
        if not directory:
            return 0

        directory = Path(directory)
        if not directory.is_dir():
            logger.warning(f"Custom skill directory does not exist: {directory}")
            return 0

        skills = load_skills_from_directory(directory)
        for skill in skills:
            if skill.name in self._skills:
                logger.info(
                    f"Custom skill '{skill.name}' overrides built-in"
                )
            self.register(skill)

        logger.info(f"Loaded {len(skills)} custom skills from {directory}")
        return len(skills)

    def load_builtin_strategies(self) -> int:
        """旧调用点使用的兼容别名，内部复用 :meth:`load_builtin_skills`。"""
        return self.load_builtin_skills()

    def load_custom_strategies(self, directory: Union[str, Path, None]) -> int:
        """旧调用点使用的兼容别名，内部复用 :meth:`load_custom_skills`。"""
        return self.load_custom_skills(directory)

    def get(self, name: str) -> Optional[Skill]:
        """按名称取一条技能，未找到时返回 ``None``。"""
        return self._skills.get(name)

    def list_skills(self) -> List[Skill]:
        """列出所有已注册技能（含未激活的）。"""
        return list(self._skills.values())

    def list_active_skills(self) -> List[Skill]:
        """仅列出当前处于激活状态（``enabled=True``）的技能。"""
        return [s for s in self._skills.values() if s.enabled]

    def activate(self, skill_names: List[str]) -> None:
        """按名称激活指定技能，并停用列表外的其他技能。

        Args:
            skill_names: 要激活的技能名称列表。传 ``["all"]`` 或包含 ``"all"``
                时全部激活。
        """
        # 特殊值 "all"：一键激活全部技能，通常用于调试或无差别推送场景。
        if skill_names == ["all"] or "all" in skill_names:
            for s in self._skills.values():
                s.enabled = True
            logger.info(f"Activated all {len(self._skills)} skills")
            return

        for s in self._skills.values():
            s.enabled = s.name in skill_names

        activated = [s.name for s in self._skills.values() if s.enabled]
        logger.info(f"Activated skills: {activated}")

    def get_skill_instructions(self) -> str:
        """将当前所有激活技能汇总为可注入到 Agent 提示词的指令字符串。

        内部按技能类别分组渲染：已知类别使用中文标签，未知类别沿用原 key。
        """
        active = self.list_active_skills()
        if not active:
            return ""

        # 按类别分组；已知英文 key 映射到中文标签，便于 LLM 阅读。
        categories = {"trend": "趋势", "pattern": "形态", "reversal": "反转", "framework": "框架"}
        grouped: Dict[str, List[Skill]] = {}
        for skill in active:
            cat = skill.category or "trend"
            grouped.setdefault(cat, []).append(skill)

        parts = []
        idx = 1
        # 先按固定顺序渲染四个已知类别，再把其他自定义类别追加到末尾。
        ordered_keys = ["trend", "pattern", "reversal", "framework"]
        for cat_key in ordered_keys + [k for k in grouped if k not in ordered_keys]:
            skills_in_cat = grouped.get(cat_key, [])
            if not skills_in_cat:
                continue
            cat_label = categories.get(cat_key, cat_key)
            parts.append(f"#### {cat_label}类技能\n")
            for skill in skills_in_cat:
                rules_ref = ""
                if skill.core_rules:
                    rules_ref = f"（关联核心理念：第{'、'.join(str(r) for r in skill.core_rules)}条）"
                support_ref = ""
                if skill.bundle_dir and skill.entrypoint.endswith("SKILL.md"):
                    support_ref = "（bundle）"
                parts.append(
                    f"### 技能 {idx}: {skill.display_name} {rules_ref}{support_ref}\n\n"
                    f"**适用场景**: {skill.description}\n\n"
                    f"{skill.instructions}\n"
                )
                idx += 1

        return "\n".join(parts)

    def get_required_tools(self) -> List[str]:
        """汇总当前所有激活技能声明依赖的工具名列表（去重后）。"""
        tools: set = set()
        for s in self.list_active_skills():
            tools.update(s.required_tools)
        return list(tools)
