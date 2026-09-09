# -*- coding: utf-8 -*-
"""Agent API endpoints.

这些接口提供 Agent 对话、技能/策略目录、会话历史、通知通道转发、研究任务和 SSE
流式输出。用户维度的配额、积分扣减和 session_id 隔离都在 endpoint 层进入执行器前完成。
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.deps import get_current_user, get_db
from src.config import get_config
from src.services.agent_model_service import list_agent_model_deployments
from src.storage import AppUser
from src.users import (
    KIND_AGENT,
    enforce_quota,
    quota_exceeded_payload,
    refund_quota,
)
from src.users.credits import (
    CreditOutcome,
    credit_exceeded_payload,
    enforce_credits,
    refund_consumed_credits,
    refund_credits,
)

# Tool name -> Chinese display name mapping
TOOL_DISPLAY_NAMES: Dict[str, str] = {
    "get_realtime_quote":         "获取实时行情",
    "get_daily_history":          "获取历史K线",
    "get_chip_distribution":      "分析筹码分布",
    "get_analysis_context":       "获取分析上下文",
    "get_stock_info":             "获取股票基本面",
    "search_stock_news":          "搜索股票新闻",
    "search_comprehensive_intel": "搜索综合情报",
    "analyze_trend":              "分析技术趋势",
    "calculate_ma":               "计算均线系统",
    "get_volume_analysis":        "分析量能变化",
    "analyze_pattern":            "识别K线形态",
    "get_market_indices":         "获取市场指数",
    "get_sector_rankings":        "分析行业板块",
    "get_skill_backtest_summary": "获取技能回测概览",
    "get_strategy_backtest_summary": "获取策略回测概览",
    "get_stock_backtest_summary": "获取个股回测数据",
}

logger = logging.getLogger(__name__)

router = APIRouter()


def _current_user_id_or_none(current_user: Any) -> Optional[int]:
    """从 AppUser 之类的对象中提取数值型 user id。"""
    user_id = getattr(current_user, "id", None)
    if user_id is None:
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def _db_user(db: Session, current_user: AppUser) -> AppUser:
    """在配额/积分扣减前尽量从 DB 重新加载当前用户，保证状态最新。"""
    user_id = _current_user_id_or_none(current_user)
    if user_id is None or not hasattr(db, "query"):
        return current_user
    try:
        row = db.query(AppUser).filter(AppUser.id == user_id).first()
    except Exception:  # noqa: BLE001
        # DB 不可用时直接复用请求上下文中的 user 对象
        return current_user
    return row if isinstance(row, AppUser) else current_user


# To C 多用户隔离: 登录用户的 session_id 被加上 ``u{user_id}:`` 前缀,
# 走 :func:`extract_user_id_from_session` 反解 并把消息写入对应 user 名下;
# Bot / CLI 路径不走这里, session_id 保持原样。
def _scope_session_id(
    raw_session_id: Optional[str],
    current_user: Optional[AppUser],
) -> str:
    """在登录态下为会话 id 加 ``u{user_id}:`` 前缀，防止跨用户访问历史。"""
    if current_user is None:
        return raw_session_id or str(uuid.uuid4())
    prefix = f"u{current_user.id}:"
    if raw_session_id and raw_session_id.startswith(prefix):
        return raw_session_id
    inner = raw_session_id or uuid.uuid4().hex
    # 防止跨用户粘贴: 如果客户端传了另一个用户的前缀 ``u{N}:``, 脱掋后重新加。
    if inner.startswith("u") and ":" in inner and inner.split(":", 1)[0][1:].isdigit():
        inner = inner.split(":", 1)[1] or uuid.uuid4().hex
    return f"{prefix}{inner}"

class ChatRequest(BaseModel):
    """Agent 对话请求，支持会话延续、技能选择与可复用的上下文。"""

    model_config = ConfigDict(populate_by_name=True)

    message: str
    session_id: Optional[str] = None
    skills: Optional[List[str]] = Field(default=None)
    context: Optional[Dict[str, Any]] = None  # 用于复用上一次分析的上下文

    @property
    def effective_skills(self) -> Optional[List[str]]:
        """返回统一请求形态下的技能 id 列表。"""
        return self.skills

class ChatResponse(BaseModel):
    """非流式对话端点的响应体。"""

    success: bool
    content: str
    session_id: str
    error: Optional[str] = None
    selected_skill_ids: Optional[List[str]] = None

class SkillInfo(BaseModel):
    """前端技能选择器所需的最小化技能元数据。"""

    id: str
    name: str
    description: str

class SkillsResponse(BaseModel):
    """可用技能列表与配置的默认技能 id。"""

    skills: List[SkillInfo]
    default_skill_id: str = ""


class StrategiesResponse(BaseModel):
    """兼容旧前端的策略列表响应，复用技能的元数据形态。"""

    strategies: List[SkillInfo]
    default_strategy_id: str = ""


class AgentModelDeployment(BaseModel):
    """一个已配置的 Agent 模型部署选项。"""

    deployment_id: str
    model: str
    provider: str
    source: str
    api_base: Optional[str] = None
    deployment_name: Optional[str] = None
    is_primary: bool = False
    is_fallback: bool = False


class AgentModelsResponse(BaseModel):
    """已配置的 Agent 模型部署响应。"""

    models: List[AgentModelDeployment]


@router.get("/models", response_model=AgentModelsResponse)
async def get_agent_models():
    """返回前端可选的 Agent 模型部署列表。"""
    config = get_config()
    return AgentModelsResponse(
        models=[AgentModelDeployment(**item) for item in list_agent_model_deployments(config)]
    )


def _build_skills_response(config) -> SkillsResponse:
    """从运行时配置构造可用技能的元数据响应。"""
    from src.agent.factory import get_skill_manager
    from src.agent.skills.defaults import get_primary_default_skill_id

    skill_manager = get_skill_manager(config)
    # 仅暴露允许用户手动调用的技能，按优先级排序
    available_skills = sorted(
        [
            skill
            for skill in skill_manager.list_skills()
            if getattr(skill, "user_invocable", True)
        ],
        key=lambda skill: (
            int(getattr(skill, "default_priority", 100)),
            skill.display_name,
            skill.name,
        ),
    )
    skills = [
        SkillInfo(id=skill.name, name=skill.display_name, description=skill.description)
        for skill in available_skills
    ]
    return SkillsResponse(
        skills=skills,
        default_skill_id=get_primary_default_skill_id(available_skills),
    )


@router.get("/skills", response_model=SkillsResponse)
async def get_skills():
    """返回当前可用的 Agent 策略技能列表。"""
    return _build_skills_response(get_config())


@router.get("/strategies", response_model=StrategiesResponse, include_in_schema=False)
async def get_strategies():
    """兼容旧客户端的 /strategies 别名端点。"""
    payload = _build_skills_response(get_config())
    return StrategiesResponse(
        strategies=payload.skills,
        default_strategy_id=payload.default_skill_id,
    )

@router.post("/chat", response_model=ChatResponse)
async def agent_chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """与 AI Agent 进行同步对话。"""
    config = get_config()

    if not config.is_agent_available():
        raise HTTPException(status_code=400, detail="Agent mode is not enabled")

    current_user_id = _current_user_id_or_none(current_user)
    if current_user_id is not None:
        current_user = _db_user(db, current_user)
        current_user_id = _current_user_id_or_none(current_user)
    effective_user = current_user if current_user_id is not None else None
    outcome = None
    credit_outcome: Optional[CreditOutcome] = None
    if current_user_id is not None:
        outcome = enforce_quota(db, user=current_user, kind=KIND_AGENT)
        if outcome.exceeded:
            db.commit()
            return JSONResponse(status_code=402, content=quota_exceeded_payload(outcome))
        credit_outcome = enforce_credits(db, user=current_user, kind=KIND_AGENT, related_type="agent")
        if credit_outcome.exceeded:
            # 积分不足时回滚本次已扣的日额度，保持一致
            if outcome.consumed:
                refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
            db.commit()
            return JSONResponse(status_code=402, content=credit_exceeded_payload(credit_outcome))
        if outcome.consumed or credit_outcome.consumed:
            db.commit()

    session_id = _scope_session_id(request.session_id, effective_user)

    try:
        from src.services.agent_chat_session_service import AgentChatSessionService

        session_service = AgentChatSessionService()
        selection = session_service.resolve_skill_selection(
            config, session_id, request.effective_skills,
        )
        skills = selection.effective_skill_ids
        session_service.persist_skill_selection(session_id, selection.selected_skill_ids_update)
        if current_user_id is None:
            executor = _build_executor(config, skills or None)
        else:
            executor = _build_executor(config, skills or None, user_id=current_user_id)

        # 将显式传入的 skills 注入到 context，覆盖 context 中可能残留的旧值
        ctx = dict(request.context or {})
        if skills is not None:
            ctx["skills"] = skills

        # 把阻塞型执行器调用 offload 到线程池，避免长时间占用事件循环
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: executor.chat(message=request.message, session_id=session_id,
                                  context=ctx),
        )

        result_success = bool(getattr(result, "success", False))
        if outcome and outcome.consumed and not result_success:
            refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
        if credit_outcome and credit_outcome.consumed and not result_success:
            refund_consumed_credits(db, user=current_user, outcome=credit_outcome, related_type="agent")
        if ((outcome and outcome.consumed) or (credit_outcome and credit_outcome.consumed)) and not result_success:
            db.commit()

        return ChatResponse(
            success=result_success,
            content=result.content,
            session_id=session_id,
            error=result.error,
            selected_skill_ids=skills,
        )

    except Exception as e:
        if credit_outcome and credit_outcome.consumed:
            refund_consumed_credits(db, user=current_user, outcome=credit_outcome, related_type="agent")
        if outcome and outcome.consumed:
            refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
        if (outcome and outcome.consumed) or (credit_outcome and credit_outcome.consumed):
            db.commit()
        logger.error(f"Agent chat API failed: {e}")
        logger.exception("Agent chat error details:")
        raise HTTPException(status_code=500, detail=str(e))


class SessionItem(BaseModel):
    """单条持久化 Agent 会话的摘要行。"""

    session_id: str
    title: str
    message_count: int
    created_at: Optional[str] = None
    last_active: Optional[str] = None

class SessionsResponse(BaseModel):
    """聊天历史会话列表的分页响应。"""

    sessions: List[SessionItem]

class SessionMessagesResponse(BaseModel):
    """单个 Agent 会话的消息列表响应。"""

    session_id: str
    messages: List[Dict[str, Any]]
    selected_skill_ids: Optional[List[str]] = None


@router.get("/chat/sessions", response_model=SessionsResponse)
async def list_chat_sessions(
    limit: int = 50,
    user_id: Optional[str] = None,
    current_user: AppUser = Depends(get_current_user),
):
    """获取当前用户的聊天会话列表。

    Args:
        limit: 返回的最大会话数量。
        user_id: 可选的平台级用户标识（如 ``telegram_12345``、``feishu_ou_abc``），
            仅用于 Bot / CLI 路径的隔离；登录用户请勿传入，后端以 ``current_user`` 为准。
    """
    from src.storage import get_db
    sessions = get_db().get_chat_sessions(
        limit=limit,
        user_id=current_user.id,
    )
    return SessionsResponse(sessions=sessions)


def _ensure_session_owner(
    session_id: str,
    current_user: Optional[AppUser],
) -> None:
    """登录态下拒绝访问不属于当前用户的会话。"""
    if current_user is None:
        return
    expected_prefix = f"u{current_user.id}:"
    if not session_id.startswith(expected_prefix):
        raise HTTPException(status_code=404, detail="session not found")


@router.get("/chat/sessions/{session_id}", response_model=SessionMessagesResponse)
async def get_chat_session_messages(
    session_id: str,
    limit: int = 100,
    current_user: AppUser = Depends(get_current_user),
):
    """获取单个会话的完整消息列表。"""
    from src.storage import get_db
    _ensure_session_owner(session_id, current_user)
    storage = get_db()
    messages = storage.get_conversation_messages(session_id, limit=limit)
    return SessionMessagesResponse(
        session_id=session_id, messages=messages,
        selected_skill_ids=storage.get_conversation_session_selected_skill_ids(session_id),
    )


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    current_user: AppUser = Depends(get_current_user),
):
    """删除指定会话及其关联消息。"""
    from src.storage import get_db
    _ensure_session_owner(session_id, current_user)
    count = get_db().delete_conversation_session(session_id)
    return {"deleted": count}


class SendChatRequest(BaseModel):
    """将聊天内容推送到通知渠道的请求体。"""

    content: str = Field(..., min_length=1, max_length=50000)
    title: Optional[str] = None


@router.post("/chat/send")
async def send_chat_to_notification(request: SendChatRequest):
    """将对话内容推送到已配置的通知渠道，避免阻塞事件循环。"""
    from src.notification import NotificationService

    loop = asyncio.get_running_loop()
    success = await loop.run_in_executor(
        None,
        lambda: NotificationService().send(request.content),
    )
    if not success:
        return {
            "success": False,
            "error": "no_channels",
            "message": "未配置通知渠道，请先在设置中配置",
        }
    return {"success": True}


def _build_executor(config, skills: Optional[List[str]] = None, user_id: Optional[int] = None):
    """构造一个配置好的 AgentExecutor（同步辅助函数）。"""
    from src.agent.factory import build_agent_executor
    return build_agent_executor(config, skills=skills, user_id=user_id)


async def _run_research_in_background(
    agent,
    question: str,
    context: Optional[Dict[str, Any]],
    *,
    timeout: int,
):
    """在事件循环之外执行深度研究，并施加内部整体超时。"""
    return await asyncio.to_thread(
        agent.research,
        question,
        context,
        timeout_seconds=timeout,
    )


# ============================================================
# Deep research endpoint
# ============================================================

class ResearchRequest(BaseModel):
    """深度研究请求，支持自由提问或附带股票上下文。"""

    question: str
    stock_code: Optional[str] = None

class ResearchResponse(BaseModel):
    """深度研究响应，包含报告内容、来源列表与 token 消耗。"""

    success: bool
    content: str
    sources: List[str] = Field(default_factory=list)
    token_usage: int = 0
    error: Optional[str] = None


@router.post("/research", response_model=ResearchResponse)
async def agent_research(
    request: ResearchRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """通过 ResearchAgent 执行深度研究查询。

    与 ``/research`` bot 命令类似，但作为 REST 端点对外暴露。
    """
    config = get_config()
    if not config.is_agent_available():
        raise HTTPException(status_code=400, detail="Agent mode is not enabled")

    # Phase 2: 深度研究同样走 agent 配额池
    current_user_id = _current_user_id_or_none(current_user)
    if current_user_id is not None:
        current_user = _db_user(db, current_user)
        current_user_id = _current_user_id_or_none(current_user)
    outcome = None
    credit_outcome: Optional[CreditOutcome] = None
    if current_user_id is not None:
        outcome = enforce_quota(db, user=current_user, kind=KIND_AGENT)
        if outcome.exceeded:
            db.commit()
            return JSONResponse(status_code=402, content=quota_exceeded_payload(outcome))
        credit_outcome = enforce_credits(db, user=current_user, kind=KIND_AGENT, related_type="research")
        if credit_outcome.exceeded:
            if outcome.consumed:
                refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
            db.commit()
            return JSONResponse(status_code=402, content=credit_exceeded_payload(credit_outcome))
        if outcome.consumed or credit_outcome.consumed:
            db.commit()

    question = request.question
    context: Optional[Dict[str, Any]] = None
    if request.stock_code:
        # 在问题前缀注入股票上下文，方便下游 LLM 与工具识别标的
        question = f"[Stock: {request.stock_code}] {question}"
        context = {"stock_code": request.stock_code}

    try:
        from src.agent.research import ResearchAgent
        from src.agent.factory import get_tool_registry
        from src.agent.llm_adapter import LLMToolAdapter

        registry = get_tool_registry()
        llm_adapter = LLMToolAdapter(config, user_id=current_user_id)
        # token 预算、子问题上限等均走配置，便于在不重启的情况下灵活调整
        budget = getattr(config, "agent_deep_research_budget", 30000)
        max_sub_questions = getattr(config, "agent_deep_research_max_sub_questions", 8)
        sub_question_steps = getattr(config, "agent_deep_research_sub_question_steps", 6)

        agent = ResearchAgent(
            tool_registry=registry,
            llm_adapter=llm_adapter,
            token_budget=budget,
            max_sub_questions=max_sub_questions,
            sub_question_max_steps=sub_question_steps,
        )

        research_timeout = getattr(config, "agent_deep_research_timeout", 600)

        result = await _run_research_in_background(
            agent,
            question,
            context,
            timeout=research_timeout,
        )
        if getattr(result, "timed_out", False):
            logger.warning("Agent research API timed out after %ss", research_timeout)
            # 整体超时：退还本次已扣的积分与日额度
            if credit_outcome and credit_outcome.consumed:
                refund_consumed_credits(db, user=current_user, outcome=credit_outcome, related_type="research")
            if outcome and outcome.consumed:
                refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
            if (outcome and outcome.consumed) or (credit_outcome and credit_outcome.consumed):
                db.commit()
            return ResearchResponse(
                success=False,
                content="",
                sources=[],
                token_usage=0,
                error=f"Deep research timed out after {research_timeout}s",
            )

        result_success = bool(getattr(result, "success", False))
        if credit_outcome and credit_outcome.consumed and not result_success:
            refund_consumed_credits(db, user=current_user, outcome=credit_outcome, related_type="research")
        if outcome and outcome.consumed and not result_success:
            refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
        if ((outcome and outcome.consumed) or (credit_outcome and credit_outcome.consumed)) and not result_success:
            db.commit()

        return ResearchResponse(
            success=result_success,
            content=result.report,
            sources=[f"Sub-question {i+1}: {q}" for i, q in enumerate(result.sub_questions)],
            token_usage=result.total_tokens,
            error=result.error if not result_success else None,
        )
    except Exception as e:
        if credit_outcome and credit_outcome.consumed:
            refund_consumed_credits(db, user=current_user, outcome=credit_outcome, related_type="research")
        if outcome and outcome.consumed:
            refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
        if (outcome and outcome.consumed) or (credit_outcome and credit_outcome.consumed):
            db.commit()
        logger.error("Agent research API failed: %s", e)
        logger.exception("Agent research error details:")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def agent_chat_stream(
    request: ChatRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """与 AI Agent 对话，并通过 SSE 实时推送进度。

    每个 SSE 事件为带 ``type`` 字段的 JSON 对象：
    - thinking: Agent 正在决策下一步行动
    - tool_start: 一次工具调用已开始
    - tool_done: 一次工具调用完成
    - generating: 最终答复生成中
    - done: 分析完成，包含 ``content`` 与 ``success``
    - error: 发生错误，包含 ``message``
    """
    config = get_config()
    if not config.is_agent_available():
        raise HTTPException(status_code=400, detail="Agent mode is not enabled")

    # Phase 2: 流式问股仍按 1 次配额扣减; SSE 链路上失败时由 outer scope refund
    current_user_id = _current_user_id_or_none(current_user)
    if current_user_id is not None:
        current_user = _db_user(db, current_user)
        current_user_id = _current_user_id_or_none(current_user)
    else:
        current_user = None
    outcome = None
    credit_outcome: Optional[CreditOutcome] = None
    if current_user_id is not None:
        outcome = enforce_quota(db, user=current_user, kind=KIND_AGENT)
        if outcome.exceeded:
            db.commit()
            return JSONResponse(status_code=402, content=quota_exceeded_payload(outcome))
        credit_outcome = enforce_credits(db, user=current_user, kind=KIND_AGENT, related_type="agent_stream")
        if credit_outcome.exceeded:
            if outcome.consumed:
                refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
            db.commit()
            return JSONResponse(status_code=402, content=credit_exceeded_payload(credit_outcome))
        if outcome.consumed or credit_outcome.consumed:
            db.commit()

    session_id = _scope_session_id(request.session_id, current_user)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    # 将显式传入的 skills 注入 context，覆盖可能的旧值
    from src.services.agent_chat_session_service import AgentChatSessionService
    session_service = AgentChatSessionService()
    selection = session_service.resolve_skill_selection(
        config, session_id, request.effective_skills,
    )
    skills = selection.effective_skill_ids
    session_service.persist_skill_selection(session_id, selection.selected_skill_ids_update)
    stream_ctx = dict(request.context or {})
    if skills is not None:
        stream_ctx["skills"] = skills

    # 在 SSE 链路里捕获最终结果, 用于决定是否 refund
    stream_result: Dict[str, Any] = {"failed": False}

    def progress_callback(event: dict):
        """把执行器回调事件从工作线程桥接到 SSE 队列。"""
        # 为工具事件补充中文展示名，便于前端展示
        if event.get("type") in ("tool_start", "tool_done"):
            tool = event.get("tool", "")
            event["display_name"] = TOOL_DISPLAY_NAMES.get(tool, tool)
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    _stream_user_id = current_user_id

    def run_sync():
        """在工作线程中执行阻塞型 Agent 对话，并发布收尾事件。"""
        try:
            executor = _build_executor(config, skills or None, user_id=_stream_user_id)
            result = executor.chat(
                message=request.message,
                session_id=session_id,
                progress_callback=progress_callback,
                context=stream_ctx,
            )
            if not getattr(result, "success", False):
                stream_result["failed"] = True
            asyncio.run_coroutine_threadsafe(
                queue.put({
                    "type": "done",
                    "success": result.success,
                    "content": result.content,
                    "error": result.error,
                    "total_steps": result.total_steps,
                    "session_id": session_id,
                    "selected_skill_ids": skills,
                }),
                loop,
            )
        except Exception as exc:
            stream_result["failed"] = True
            logger.error(f"Agent stream error: {exc}")
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "error", "message": str(exc)}),
                loop,
            )

    async def event_generator():
        """将 Agent 进度事件以 SSE 帧形式产出，直到 done/error/超时。"""
        # 把执行器放到后台线程，避免阻塞事件循环
        fut = loop.run_in_executor(None, run_sync)
        try:
            while True:
                try:
                    # 设置较长超时以兼容慢查询；超时本身视为失败并推送 error 帧
                    event = await asyncio.wait_for(queue.get(), timeout=300.0)
                except asyncio.TimeoutError:
                    stream_result["failed"] = True
                    yield "data: " + json.dumps({"type": "error", "message": "分析超时"}, ensure_ascii=False) + "\n\n"
                    break
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                if event.get("type") in ("done", "error"):
                    if event.get("type") == "error":
                        stream_result["failed"] = True
                    break
        finally:
            try:
                # 给后台线程一个短暂的清理窗口
                await asyncio.wait_for(fut, timeout=5.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                # 清理超过 5s 视为正常超时，不告警
                logger.debug("agent executor cleanup timed out after 5s for session %s", session_id)
            except Exception as exc:
                logger.warning("agent executor cleanup error (ignored): %s", exc, exc_info=True)
            # SSE 链路结束: 失败时退回配额 (避免连续失败榨干用户额度)。
            # 注意: 外层 get_db 注入的 session 在 endpoint 返回后已关闭, 这里用单独的 session。
            if stream_result["failed"] and outcome and outcome.consumed and current_user_id is not None:
                refund_user_id = current_user_id
                refund_kind = KIND_AGENT
                refund_date = outcome.on_date
                try:
                    from src.storage import DatabaseManager
                    from src.users.quota import refund as _quota_refund

                    refund_session = DatabaseManager.get_instance().get_session()
                    try:
                        _quota_refund(
                            refund_session,
                            user_id=refund_user_id,
                            kind=refund_kind,
                            on_date=refund_date,
                        )
                        refund_session.commit()
                    finally:
                        refund_session.close()
                except Exception as refund_exc:
                    logger.warning(
                        "agent stream quota refund failed (ignored): %s",
                        refund_exc,
                        exc_info=True,
                    )
            if stream_result["failed"] and credit_outcome and credit_outcome.consumed and current_user_id is not None:
                try:
                    from src.storage import DatabaseManager

                    credit_session = DatabaseManager.get_instance().get_session()
                    try:
                        user = credit_session.query(AppUser).filter(AppUser.id == current_user_id).first()
                        if user is not None:
                            refund_credits(
                                credit_session,
                                user=user,
                                amount=credit_outcome.cost,
                                kind=KIND_AGENT,
                                related_type="agent_stream",
                                related_id=session_id,
                                idempotency_key=f"agent-stream-credit-refund:{credit_outcome.ledger_id}",
                            )
                            credit_session.commit()
                    finally:
                        credit_session.close()
                except Exception as refund_exc:
                    logger.warning(
                        "agent stream credit refund failed (ignored): %s",
                        refund_exc,
                        exc_info=True,
                    )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
