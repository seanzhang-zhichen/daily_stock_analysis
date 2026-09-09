"""分析生成后端的共享契约（contract）层。

定义所有具体生成后端（如远程 API 后端、本地 CLI 后端）需要遵循的统一接口与结果/异常类型，
让上层调用方（如分析流水线、调度服务）可以以一致方式与不同后端交互。

主要导出：

- :class:`GenerationErrorCode`：生成失败的分类错误码枚举。
- :class:`GenerationResult`：成功生成时的结构化结果。
- :class:`GenerationError`：统一的异常类型，携带错误码、阶段、重试/降级标记。
- :class:`GenerationBackend`：Protocol 形式的生成接口约定。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Protocol

class GenerationErrorCode(str, Enum):
    """生成失败的统一分类错误码。

    字符串取自后端实际发生的失败语义，便于上层按码做策略路由（例如 retryable 的失败可走退避重试）。

    枚举值：

    - ``BACKEND_NOT_CONFIGURED``：后端环境变量 / 密钥缺失；
    - ``COMMAND_NOT_FOUND``：本地 CLI 后端找不到可执行文件；
    - ``TIMEOUT``：调用超时；
    - ``NON_ZERO_EXIT``：本地 CLI 返回非 0 退出码；
    - ``EMPTY_OUTPUT``：成功调用但无输出内容；
    - ``OUTPUT_TOO_LARGE``：单次输出超过限额；
    - ``UNSAFE_CONFIG``：检测到禁用 / 不安全配置；
    - ``UNKNOWN_BACKEND_ERROR``：其他未知异常。
    """
    BACKEND_NOT_CONFIGURED = "backend_not_configured"
    COMMAND_NOT_FOUND = "command_not_found"
    TIMEOUT = "timeout"
    NON_ZERO_EXIT = "non_zero_exit"
    EMPTY_OUTPUT = "empty_output"
    OUTPUT_TOO_LARGE = "output_too_large"
    UNSAFE_CONFIG = "unsafe_config"
    UNKNOWN_BACKEND_ERROR = "unknown_backend_error"

@dataclass
class GenerationResult:
    """生成成功的结构化结果。

    Attributes:
        text: 生成的文本主体（一般是模型最终输出）。
        model: 实际使用的模型标识。
        provider: 模型提供方（厂商/平台名）。
        backend: 触达该 provider 的后端实现名。
        usage: 用量统计（如 token 数、计费字段），由各后端自行填充。
        raw: 后端原始响应对象，便于排查问题或抽取额外字段。
        diagnostics: 诊断信息（如耗时、退避次数、流式增量大小等）。
    """
    text: str
    model: str
    provider: str
    backend: str
    # 用量统计由具体后端填充；空 dict 表示未知
    usage: Dict[str, Any] = field(default_factory=dict)
    # 后端原始响应，便于排错或做额外字段抽取
    raw: Any = None
    # 诊断元数据（耗时、重试次数、流式增量等）
    diagnostics: Dict[str, Any] = field(default_factory=dict)

@dataclass
class GenerationError(Exception):
    """生成失败的统一异常。

    Attributes:
        error_code: 错误分类码，便于上层做差异化处理。
        stage: 失败发生的阶段（如 ``load``、``invoke``、``parse``），便于排障。
        retryable: 是否可走退避重试。
        fallbackable: 是否允许切换到下一个后端做降级。
        backend: 触发失败的后端实现名。
        provider: 触发失败的 provider，未显式传入时回退为 backend。
        details: 失败相关的额外上下文（堆栈摘要、退出码等）。
    """
    error_code: GenerationErrorCode
    stage: str
    retryable: bool
    fallbackable: bool
    backend: str
    provider: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """补齐 provider 缺省值，并完成父类 Exception 初始化。"""
        # provider 缺省时使用 backend，保证异常文本与日志里始终有 provider 信息
        self.provider = self.provider or self.backend
        # 调用父类初始化，确保能被标准 Exception 捕获链路处理
        Exception.__init__(self, self.message)

    @property
    def message(self) -> str:
        """返回人类可读的异常消息，格式固定便于日志聚合。"""
        return f"{self.error_code.value} at {self.stage} for backend {self.backend}"

class GenerationBackend(Protocol):
    """生成后端必须实现的接口约定（Protocol，无需显式继承）。

    调用方以结构化子类型方式使用不同后端，无需关心其具体类。
    各方法参数说明：

    - ``prompt``：用户提示词；
    - ``generation_config``：生成参数（temperature / max_tokens 等）；
    - ``system_prompt``：可选 system prompt；
    - ``stream``：是否走流式通道；
    - ``stream_progress_callback``：流式增量回调；
    - ``response_validator``：用于提前校验 LLM 输出的可调用对象；
    - ``audit_context``：审计 / 成本统计上下文。
    """
    def generate(self, prompt: str, generation_config: Dict[str, Any], *, system_prompt: Optional[str] = None,
                 stream: bool = False, stream_progress_callback: Optional[Callable[[int], None]] = None,
                 response_validator: Optional[Callable[[str], None]] = None,
                 audit_context: Optional[Dict[str, Any]] = None) -> GenerationResult:
        """执行一次文本生成并返回结构化结果（Protocol 桩方法）。"""
        ...
