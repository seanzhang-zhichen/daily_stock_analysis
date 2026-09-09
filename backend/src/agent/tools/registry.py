# -*- coding: utf-8 -*-
"""
Agent 框架的工具注册表。

提供：
- ToolParameter / ToolDefinition 数据类
- ToolRegistry：支持多供应商 schema 生成的中央工具注册表
- @tool 装饰器，便于注册工具
"""

import json
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 数据类
# ============================================================

@dataclass
class ToolParameter:
    """单个工具参数的 schema。"""
    name: str
    type: str  # "string" | "number" | "integer" | "boolean" | "array" | "object"
    description: str
    required: bool = True
    enum: Optional[List[str]] = None
    default: Any = None


@dataclass
class ToolDefinition:
    """Agent 可调用工具的完整定义。"""
    name: str
    description: str
    parameters: List[ToolParameter]
    handler: Callable
    category: str = "data"  # data | analysis | search | action
    timeout_seconds: Optional[float] = None

    # ----- 多供应商 schema 转换器 -----

    def _params_json_schema(self) -> dict:
        """把参数转换为 JSON Schema（OpenAI/Anthropic 共用）。"""
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for p in self.parameters:
            prop: Dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            properties[p.name] = prop
            if p.required:
                required.append(p.name)
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema

    def to_openai_tool(self) -> dict:
        """转换为 OpenAI ``tools`` 列表元素格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._params_json_schema(),
            },
        }


# ============================================================
# 工具注册表
# ============================================================

class ToolRegistry:
    """所有 Agent 可调用工具的中央注册表。

    用法::

        registry = ToolRegistry()
        registry.register(tool_def)
        registry.execute("get_realtime_quote", stock_code="600519")
    """

    def __init__(self, category_timeouts: Optional[Dict[str, float]] = None):
        """创建一个空的 name -> ToolDefinition 映射。"""
        self._tools: Dict[str, ToolDefinition] = {}
        self._category_timeouts = {
            category: float(timeout)
            for category, timeout in (category_timeouts or {}).items()
            if isinstance(timeout, (int, float)) and timeout > 0
        }

    # ----- 注册 -----

    def register(self, tool_def: ToolDefinition) -> None:
        """注册一个工具定义。"""
        if tool_def.name in self._tools:
            logger.warning(f"Tool '{tool_def.name}' already registered, overwriting")
        self._tools[tool_def.name] = tool_def
        logger.debug(f"Registered tool: {tool_def.name} (category={tool_def.category})")

    def unregister(self, name: str) -> None:
        """移除一个已注册的工具。"""
        self._tools.pop(name, None)

    # ----- 查询 -----

    def get(self, name: str) -> Optional[ToolDefinition]:
        """按名称返回工具定义。"""
        return self._tools.get(name)

    def list_tools(self, category: Optional[str] = None) -> List[ToolDefinition]:
        """列出所有工具，可按分类过滤。"""
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        return tools

    def list_names(self) -> List[str]:
        """返回所有已注册的工具名称。"""
        return list(self._tools.keys())

    def __len__(self) -> int:
        """返回已注册工具的数量。"""
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        """返回某个工具名是否已注册。"""
        return name in self._tools

    def category_default_timeout(self, category: str) -> Optional[float]:
        """返回某工具分类配置的默认超时时间。"""
        # "market" 归类到 "data"，兼容历史分类命名
        return self._category_timeouts.get("data" if category == "market" else category)

    @property
    def category_timeouts(self) -> Dict[str, float]:
        """返回一份拷贝，使过滤后的注册表保留相同的超时策略。"""
        return dict(self._category_timeouts)

    # ----- schema 生成 -----

    def to_openai_tools(self) -> List[dict]:
        """生成 OpenAI 格式的工具列表（由 litellm 用于所有供应商）。"""
        return [t.to_openai_tool() for t in self._tools.values()]

    # ----- 执行 -----

    def execute(self, name: str, **kwargs) -> Any:
        """按名称执行一个已注册工具。

        返回 JSON 可序列化的结果。
        工具不存在时抛出 ``KeyError``。
        执行失败时原样抛出 handler 的异常。

        支持 Gemini 带命名空间的工具名（如 default_api:get_realtime_quote -> get_realtime_quote）。
        """
        tool_def = self._tools.get(name)
        if tool_def is None and ":" in name:
            # Gemini 可能返回 default_api:get_realtime_quote 这类带命名空间的名字
            tool_def = self._tools.get(name.split(":", 1)[-1])
        if tool_def is None:
            raise KeyError(f"Tool '{name}' not found in registry. Available: {self.list_names()}")

        return tool_def.handler(**kwargs)


# ============================================================
# @tool 装饰器
# ============================================================

# 全局默认注册表（单例模式）
_default_registry: Optional[ToolRegistry] = None


def get_default_registry() -> ToolRegistry:
    """获取或创建全局默认 ToolRegistry。"""
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
    return _default_registry


def tool(
    name: str,
    description: str,
    category: str = "data",
    parameters: Optional[List[ToolParameter]] = None,
    registry: Optional[ToolRegistry] = None,
    timeout_seconds: Optional[float] = None,
):
    """装饰器：把函数注册为 Agent 工具。

    参数可显式指定，也可从类型注解推断。

    示例::

        @tool(name="get_realtime_quote", category="data",
              description="Get real-time stock quote")
        def get_realtime_quote(stock_code: str) -> dict:
            ...
    """
    def decorator(func: Callable) -> Callable:
        """注册被装饰的函数并原样返回。"""
        # 未显式提供参数时，从类型注解推断
        params = parameters
        if params is None:
            params = _infer_parameters(func)

        tool_def = ToolDefinition(
            name=name,
            description=description,
            parameters=params,
            handler=func,
            category=category,
            timeout_seconds=timeout_seconds,
        )

        target_registry = registry or get_default_registry()
        target_registry.register(tool_def)

        # 把元数据挂到函数上，便于自省
        func._tool_definition = tool_def
        return func

    return decorator


def _infer_parameters(func: Callable) -> List[ToolParameter]:
    """从函数签名与类型注解推断 ToolParameter 列表。"""
    sig = inspect.signature(func)
    hints = getattr(func, '__annotations__', {})
    params: List[ToolParameter] = []

    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        # 跳过返回值注解，只看参数注解
        hint = hints.get(param_name, str)
        # 处理 Optional 及其他 typing 构造
        origin = getattr(hint, '__origin__', None)
        if origin is not None:
            # Optional[X] -> X，List[X] -> array，等等
            args = getattr(hint, '__args__', ())
            if origin is list or (hasattr(origin, '__name__') and origin.__name__ == 'List'):
                param_type = "array"
            elif origin is dict:
                param_type = "object"
            else:
                # Union/Optional：取第一个非 None 的类型参数
                for a in args:
                    if a is not type(None):
                        param_type = type_map.get(a, "string")
                        break
                else:
                    param_type = "string"
        else:
            param_type = type_map.get(hint, "string")

        has_default = param.default is not inspect.Parameter.empty
        tp = ToolParameter(
            name=param_name,
            type=param_type,
            description=f"Parameter: {param_name}",
            required=not has_default,
            default=param.default if has_default else None,
        )
        params.append(tp)

    return params
