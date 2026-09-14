# -*- coding: utf-8 -*-
"""
===================================
命令处理器模块
===================================

包含所有机器人命令的实现。
"""

# 导入命令基类，作为所有命令的抽象父类
from bot.commands.base import BotCommand
# 导入各具体命令类，供外部统一引用和自动注册
from bot.commands.help import HelpCommand
from bot.commands.status import StatusCommand
from bot.commands.analyze import AnalyzeCommand
from bot.commands.market import MarketCommand
from bot.commands.batch import BatchCommand
from bot.commands.ask import AskCommand
from bot.commands.chat import ChatCommand
from bot.commands.research import ResearchCommand
from bot.commands.strategies import StrategiesCommand
from bot.commands.history import HistoryCommand

# 所有可用命令类列表（用于自动注册到 CommandDispatcher）
ALL_COMMANDS = [
    HelpCommand,
    StatusCommand,
    AnalyzeCommand,
    MarketCommand,
    BatchCommand,
    AskCommand,
    ChatCommand,
    ResearchCommand,
    StrategiesCommand,
    HistoryCommand,
]

# 公开接口：外部通过 from bot.commands import ... 可直接使用的符号
__all__ = [
    'BotCommand',
    'HelpCommand',
    'StatusCommand',
    'AnalyzeCommand',
    'MarketCommand',
    'BatchCommand',
    'AskCommand',
    'ChatCommand',
    'ResearchCommand',
    'StrategiesCommand',
    'HistoryCommand',
    'ALL_COMMANDS',
]
