# -*- coding: utf-8 -*-
"""
Agent API endpoints.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
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
    user_id = getattr(current_user, "id", None)
    if user_id is None:
        return None
    return int(user_id)


# To C 多用户隔离: 登录用户的 session_id 被加上 ``u{user_id}:`` 前缀,
# 走 :func:`extract_user_id_from_session` 反解 并把消息写入对应 user 名下;
# Bot / CLI 路径不走这里, session_id 保持原样。
def _scope_session_id(
    raw_session_id: Optional[str],
    current_user: Optional[AppUser],
) -> str:
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
    model_config = ConfigDict(populate_by_name=True)

    message: str
    session_id: Optional[str] = None
    skills: Optional[List[str]] = Field(
        default=None,
        validation_alias=AliasChoices("skills", "strategies"),
    )
    context: Optional[Dict[str, Any]] = None  # Previous analysis context for data reuse

    @property
    def effective_skills(self) -> Optional[List[str]]:
        """Return skill ids from the unified request shape."""
        return self.skills

class ChatResponse(BaseModel):
    success: bool
    content: str
    session_id: str
    error: Optional[str] = None

class SkillInfo(BaseModel):
    id: str
    name: str
    description: str

class SkillsResponse(BaseModel):
    skills: List[SkillInfo]
    default_skill_id: str = ""


class StrategiesResponse(BaseModel):
    strategies: List[SkillInfo]
    default_strategy_id: str = ""


class AgentModelDeployment(BaseModel):
    deployment_id: str
    model: str
    provider: str
    source: str
    api_base: Optional[str] = None
    deployment_name: Optional[str] = None
    is_primary: bool = False
    is_fallback: bool = False


class AgentModelsResponse(BaseModel):
    models: List[AgentModelDeployment]


@router.get("/models", response_model=AgentModelsResponse)
async def get_agent_models():
    """Get configured Agent model deployments for frontend selection."""
    config = get_config()
    return AgentModelsResponse(
        models=[AgentModelDeployment(**item) for item in list_agent_model_deployments(config)]
    )


def _build_skills_response(config) -> SkillsResponse:
    from src.agent.factory import get_skill_manager
    from src.agent.skills.defaults import get_primary_default_skill_id

    skill_manager = get_skill_manager(config)
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
    """
    Get available agent strategy skills.
    """
    return _build_skills_response(get_config())


@router.get("/strategies", response_model=StrategiesResponse, include_in_schema=False)
async def get_strategies():
    """Compatibility alias for legacy clients."""
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
    """
    Chat with the AI Agent.
    """
    config = get_config()
    
    if not config.is_agent_available():
        raise HTTPException(status_code=400, detail="Agent mode is not enabled")

    current_user_id = _current_user_id_or_none(current_user)
    effective_user = current_user if current_user_id is not None else None
    outcome = None
    if current_user_id is not None:
        outcome = enforce_quota(db, user=current_user, kind=KIND_AGENT)
        if outcome.exceeded:
            db.commit()
            return JSONResponse(status_code=402, content=quota_exceeded_payload(outcome))
        if outcome.consumed:
            db.commit()

    session_id = _scope_session_id(request.session_id, effective_user)
    
    try:
        skills = request.effective_skills
        if current_user_id is None:
            executor = _build_executor(config, skills or None)
        else:
            executor = _build_executor(config, skills or None, user_id=current_user_id)

        # Pass explicit skills into context for the orchestrator.
        # Direct assignment so caller-provided skills always take precedence
        # over any stale value carried in the context dict.
        ctx = dict(request.context or {})
        if skills is not None:
            ctx["skills"] = skills

        # Offload the blocking call to a thread to avoid blocking the event loop.
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: executor.chat(message=request.message, session_id=session_id,
                                  context=ctx),
        )

        result_success = bool(getattr(result, "success", False))
        if outcome and outcome.consumed and not result_success:
            refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
            db.commit()

        return ChatResponse(
            success=result_success,
            content=result.content,
            session_id=session_id,
            error=result.error
        )
            
    except Exception as e:
        if outcome and outcome.consumed:
            refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
            db.commit()
        logger.error(f"Agent chat API failed: {e}")
        logger.exception("Agent chat error details:")
        raise HTTPException(status_code=500, detail=str(e))


class SessionItem(BaseModel):
    session_id: str
    title: str
    message_count: int
    created_at: Optional[str] = None
    last_active: Optional[str] = None

class SessionsResponse(BaseModel):
    sessions: List[SessionItem]

class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: List[Dict[str, Any]]


@router.get("/chat/sessions", response_model=SessionsResponse)
async def list_chat_sessions(
    limit: int = 50,
    user_id: Optional[str] = None,
    current_user: AppUser = Depends(get_current_user),
):
    """获取聊天会话列表

    Args:
        limit: Maximum number of sessions to return.
        user_id: Optional platform-prefixed user identifier (e.g.
            ``telegram_12345``, ``feishu_ou_abc``) for Bot / CLI 路径的
            平台级隔离; 登录用户请不要传, 后端会以 ``current_user`` 为准。
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
    """登录模式下拒绝访问不属于当前用户的会话。"""
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
    """获取单个会话的完整消息"""
    from src.storage import get_db
    _ensure_session_owner(session_id, current_user)
    messages = get_db().get_conversation_messages(session_id, limit=limit)
    return SessionMessagesResponse(session_id=session_id, messages=messages)


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    current_user: AppUser = Depends(get_current_user),
):
    """删除指定会话"""
    from src.storage import get_db
    _ensure_session_owner(session_id, current_user)
    count = get_db().delete_conversation_session(session_id)
    return {"deleted": count}


class SendChatRequest(BaseModel):
    """Request body for sending chat content to notification channels."""

    content: str = Field(..., min_length=1, max_length=50000)
    title: Optional[str] = None


@router.post("/chat/send")
async def send_chat_to_notification(request: SendChatRequest):
    """
    Send chat session content to configured notification channels.
    Uses run_in_executor to avoid blocking the event loop.
    """
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
    """Build and return a configured AgentExecutor (sync helper)."""
    from src.agent.factory import build_agent_executor
    return build_agent_executor(config, skills=skills, user_id=user_id)


async def _run_research_in_background(
    agent,
    question: str,
    context: Optional[Dict[str, Any]],
    *,
    timeout: int,
):
    """Run deep research off the event loop with an internal overall timeout."""
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
    question: str
    stock_code: Optional[str] = None

class ResearchResponse(BaseModel):
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
    """Run a deep-research query via the ResearchAgent.

    Similar to the ``/research`` bot command but exposed as a REST endpoint.
    """
    config = get_config()
    if not config.is_agent_available():
        raise HTTPException(status_code=400, detail="Agent mode is not enabled")

    # Phase 2: deep research 也走 agent 配额池
    current_user_id = _current_user_id_or_none(current_user)
    outcome = None
    if current_user_id is not None:
        outcome = enforce_quota(db, user=current_user, kind=KIND_AGENT)
        if outcome.exceeded:
            db.commit()
            return JSONResponse(status_code=402, content=quota_exceeded_payload(outcome))
        if outcome.consumed:
            db.commit()

    question = request.question
    context: Optional[Dict[str, Any]] = None
    if request.stock_code:
        question = f"[Stock: {request.stock_code}] {question}"
        context = {"stock_code": request.stock_code}

    try:
        from src.agent.research import ResearchAgent
        from src.agent.factory import get_tool_registry
        from src.agent.llm_adapter import LLMToolAdapter

        registry = get_tool_registry()
        llm_adapter = LLMToolAdapter(config, user_id=current_user_id)
        budget = getattr(config, "agent_deep_research_budget", 30000)

        agent = ResearchAgent(
            tool_registry=registry,
            llm_adapter=llm_adapter,
            token_budget=budget,
        )

        research_timeout = getattr(config, "agent_deep_research_timeout", 180)

        result = await _run_research_in_background(
            agent,
            question,
            context,
            timeout=research_timeout,
        )
        if getattr(result, "timed_out", False):
            logger.warning("Agent research API timed out after %ss", research_timeout)
            if outcome and outcome.consumed:
                refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
                db.commit()
            return ResearchResponse(
                success=False,
                content="",
                sources=[],
                token_usage=0,
                error=f"Deep research timed out after {research_timeout}s",
            )

        result_success = bool(getattr(result, "success", False))
        if outcome and outcome.consumed and not result_success:
            refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
            db.commit()

        return ResearchResponse(
            success=result_success,
            content=result.report,
            sources=[f"Sub-question {i+1}: {q}" for i, q in enumerate(result.sub_questions)],
            token_usage=result.total_tokens,
            error=result.error if not result_success else None,
        )
    except Exception as e:
        if outcome and outcome.consumed:
            refund_quota(db, user=current_user, kind=KIND_AGENT, on_date=outcome.on_date)
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
    """
    Chat with the AI Agent, streaming progress via SSE.
    Each SSE event is a JSON object with a 'type' field:
      - thinking: AI is deciding next action
      - tool_start: a tool call has begun
      - tool_done: a tool call finished
      - generating: final answer being generated
      - done: analysis complete, contains 'content' and 'success'
      - error: error occurred, contains 'message'
    """
    config = get_config()
    if not config.is_agent_available():
        raise HTTPException(status_code=400, detail="Agent mode is not enabled")

    # Phase 2: 流式问股仍按 1 次配额扣减; SSE 链路上失败时由 outer scope refund
    outcome = enforce_quota(db, user=current_user, kind=KIND_AGENT)
    if outcome.exceeded:
        db.commit()
        return JSONResponse(status_code=402, content=quota_exceeded_payload(outcome))
    if outcome.consumed:
        db.commit()

    session_id = _scope_session_id(request.session_id, current_user)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    # Pass explicit skills into context for the orchestrator.
    # Direct assignment so caller-provided skills always take precedence.
    skills = request.effective_skills
    stream_ctx = dict(request.context or {})
    if skills is not None:
        stream_ctx["skills"] = skills

    # 在 SSE 链路里捕获最终结果, 用于决定是否 refund
    stream_result: Dict[str, Any] = {"failed": False}

    def progress_callback(event: dict):
        # Enrich tool events with display names
        if event.get("type") in ("tool_start", "tool_done"):
            tool = event.get("tool", "")
            event["display_name"] = TOOL_DISPLAY_NAMES.get(tool, tool)
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    _stream_user_id = current_user.id if current_user else None

    def run_sync():
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
        # Start executor in a thread so we don't block the event loop
        fut = loop.run_in_executor(None, run_sync)
        try:
            while True:
                try:
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
                await asyncio.wait_for(fut, timeout=5.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                # Cleanup taking longer than 5s is treated as an expected timeout; no warning.
                logger.debug("agent executor cleanup timed out after 5s for session %s", session_id)
            except Exception as exc:
                logger.warning("agent executor cleanup error (ignored): %s", exc, exc_info=True)
            # SSE 链路结束: 失败时退回配额 (避免连续失败榨干用户额度)。
            # 注意: 外层 get_db 注入的 session 在 endpoint 返回后已关闭, 这里用单独的 session。
            if stream_result["failed"] and outcome.consumed:
                refund_user_id = current_user.id
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

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
