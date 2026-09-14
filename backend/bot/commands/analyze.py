# -*- coding: utf-8 -*-
"""
===================================
股票分析命令
===================================

分析指定股票，调用 AI 生成分析报告。
"""

import logging
from typing import List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse
from src.services.stock_list_parser import ParseStatus, parse_analysis_target

logger = logging.getLogger(__name__)


class AnalyzeCommand(BotCommand):
    """
    股票分析命令
    
    分析指定股票代码或已登记指数，生成 AI 分析报告并推送。
    
    用法：
        /analyze 600519       - 分析贵州茅台（精简报告）
        /analyze 600519 full  - 分析并生成完整报告
        /analyze sh000016     - 分析上证50指数
        /analyze 上证50       - 按注册名称分析上证50指数
    """
    
    @property
    def name(self) -> str:
        """返回命令主名称，用于 `/analyze` 路由注册。"""
        return "analyze"
    
    @property
    def aliases(self) -> List[str]:
        """返回分析命令的短别名和中文触发词。"""
        return ["a", "分析", "查"]
    
    @property
    def description(self) -> str:
        """返回帮助列表中展示的命令简述。"""
        return "分析指定股票或指数"
    
    @property
    def usage(self) -> str:
        """返回帮助详情中展示的参数格式。"""
        return "/analyze <股票代码/指数代码/指数名称> [full]"
    
    def validate_args(self, args: List[str]) -> Optional[str]:
        """验证参数"""
        if not args or not (args[0] or "").strip():
            return "请输入股票代码或指数名称"
        return None
    
    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """执行分析命令"""
        target = parse_analysis_target(args[0])
        if target.asset_type == ParseStatus.UNSUPPORTED:
            reason = target.unsupported_reason or "无法识别标的"
            return BotResponse.error_response(f"无法分析 `{args[0]}`：{reason}")
        code = target.canonical_id
        analysis_target = target if target.asset_type == ParseStatus.INDEX else None
        
        # 检查是否需要完整报告（默认精简，传 full/完整/详细 切换）
        report_type = "simple"
        if len(args) > 1 and args[1].lower() in ["full", "完整", "详细"]:
            report_type = "full"
        logger.info(f"[AnalyzeCommand] 分析标的: {code}, 报告类型: {report_type}")
        
        try:
            # 调用分析服务
            from src.services.task_service import get_task_service
            from src.enums import ReportType
            
            service = get_task_service()
            
            # 提交异步分析任务
            result = service.submit_analysis(
                code=code,
                report_type=ReportType.from_str(report_type),
                source_message=message,
                analysis_target=analysis_target,
            )
            
            if result.get("success"):
                task_id = result.get("task_id", "")
                return BotResponse.markdown_response(
                    f"✅ **分析任务已提交**\n\n"
                    f"• 标的: `{code}`\n"
                    f"• 报告类型: {ReportType.from_str(report_type).display_name}\n"
                    f"• 任务 ID: `{task_id[:20]}...`\n\n"
                    f"分析完成后将自动推送结果。"
                )
            else:
                error = result.get("error", "未知错误")
                return BotResponse.error_response(f"提交分析任务失败: {error}")
                
        except Exception as e:
            logger.error(f"[AnalyzeCommand] 执行失败: {e}")
            return BotResponse.error_response(f"分析失败: {str(e)[:100]}")
