# -*- coding: utf-8 -*-
"""对话历史与 LLM 调用审计相关的存储模型。

集中放四个与"对话上下文"和"LLM 调用量"相关的 ORM 表：

- :class:`ConversationMessage`：单条对话消息；
- :class:`ConversationSessionState`：会话级 Agent 技能选择；
- :class:`ConversationSummary`：会话滚动摘要（用来压缩上下文）；
- :class:`LLMUsage`：每次 ``litellm.completion()`` 调用的审计日志。
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from src.storage.base import Base


class ConversationMessage(Base):
    """Agent 对话历史记录表。"""
    __tablename__ = 'conversation_messages'

    # 自增主键
    id = Column(Integer, primary_key=True, autoincrement=True)
    # To C 多用户隔离: Web 用户由 endpoint 注入 current_user.id；Bot / CLI 路径保持 NULL。
    user_id = Column(Integer, nullable=True, index=True)
    # 会话 ID；按 session 维度查询消息时使用，并参与索引
    session_id = Column(String(100), index=True, nullable=False)
    # 消息角色：user / assistant / system
    role = Column(String(20), nullable=False)
    # 消息正文；Text 类型避免长度截断
    content = Column(Text, nullable=False)
    # 创建时间，用于按时间窗口清理 / 排序
    created_at = Column(DateTime, default=datetime.now, index=True)


class ConversationSessionState(Base):
    """会话级 Agent 技能选择（持久化用户偏好）。"""
    __tablename__ = "conversation_session_states"

    # 以 session_id 作为主键：一个会话只对应一份技能选择
    session_id = Column(String(100), primary_key=True)
    # 用 JSON 字符串保存技能 ID 列表；避免引入关联表的复杂度
    selected_skill_ids_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class ConversationSummary(Base):
    """会话滚动摘要，用于压缩超长 Agent 对话上下文。"""
    __tablename__ = "conversation_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 一个 session 一条 summary；唯一约束保证不会被重复插入
    session_id = Column(String(100), nullable=False, unique=True, index=True)
    # 摘要正文
    summary = Column(Text, nullable=False)
    # 当前摘要已覆盖到的最大消息 ID，便于增量更新
    covered_message_id = Column(Integer, nullable=False, default=0)
    # 摘要所对应的原始消息条数，便于审计
    source_message_count = Column(Integer, nullable=False, default=0)
    # 摘要的估算 token 数，方便上层按预算选择上下文
    estimated_tokens = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)


class LLMUsage(Base):
    """每次 ``litellm.completion()`` 调用一条记录，作为 token 用量审计日志。"""
    __tablename__ = 'llm_usage'

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 调用类型枚举：'analysis' | 'agent' | 'market_review'
    call_type = Column(String(32), nullable=False, index=True)
    # 实际使用的模型标识
    model = Column(String(128), nullable=False)
    # 关联股票代码（可选；非股票类调用为空）
    stock_code = Column(String(16), nullable=True)
    # 输入 token 数
    prompt_tokens = Column(Integer, nullable=False, default=0)
    # 输出 token 数
    completion_tokens = Column(Integer, nullable=False, default=0)
    # 总 token 数（一般 = prompt + completion）
    total_tokens = Column(Integer, nullable=False, default=0)
    # 调用发生的时间
    called_at = Column(DateTime, default=datetime.now, index=True)


__all__ = ["ConversationMessage", "ConversationSessionState", "ConversationSummary", "LLMUsage"]
