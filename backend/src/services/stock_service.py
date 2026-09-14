# -*- coding: utf-8 -*-
"""股票数据服务层。

职责：
1. 封装股票数据获取逻辑
2. 提供实时行情与历史数据接口
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from src.repositories.stock_repo import StockRepository

logger = logging.getLogger(__name__)


class StockService:
    """
    股票数据服务
    
    封装股票数据获取的业务逻辑
    """
    
    def __init__(self):
        """初始化股票数据服务，构造仓储对象。"""
        self.repo = StockRepository()

    def get_realtime_quote(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        获取单只股票的实时行情快照。

        Args:
            stock_code: 股票代码。

        Returns:
            标准化的行情字典；行情源缺失或返回空时回落到占位数据 / None。
        """
        try:
            # 调用数据获取器获取实时行情
            # DataFetcherManager 是数据提供层的统一入口，负责聚合多个数据源（Tushare、Sina、AKShare 等）
            # 并根据配置的策略自动选择最优数据源返回标准化行情
            from data_provider.base import DataFetcherManager

            manager = DataFetcherManager()
            quote = manager.get_realtime_quote(stock_code)

            if quote is None:
                # 数据源返回 None 通常意味着：1) 股票代码不存在；2) 数据源全部超时或不可用
                logger.warning(f"获取 {stock_code} 实时行情失败")
                return None

            # UnifiedRealtimeQuote 是 dataclass，使用 getattr 安全访问字段
            # 不同数据源的字段命名可能存在差异，这里通过统一映射表屏蔽底层差异
            # 字段映射: UnifiedRealtimeQuote -> API 响应
            # - code -> stock_code
            # - name -> stock_name
            # - price -> current_price
            # - change_amount -> change
            # - change_pct -> change_percent
            # - open_price -> open
            # - high -> high
            # - low -> low
            # - pre_close -> prev_close
            # - volume -> volume
            # - amount -> amount
            return {
                "stock_code": getattr(quote, "code", stock_code),
                "stock_name": getattr(quote, "name", None),
                "current_price": getattr(quote, "price", 0.0) or 0.0,
                "change": getattr(quote, "change_amount", None),
                "change_percent": getattr(quote, "change_pct", None),
                "open": getattr(quote, "open_price", None),
                "high": getattr(quote, "high", None),
                "low": getattr(quote, "low", None),
                "prev_close": getattr(quote, "pre_close", None),
                "volume": getattr(quote, "volume", None),
                "amount": getattr(quote, "amount", None),
                "update_time": datetime.now().isoformat(),
            }
            
        except ImportError:
            # DataFetcherManager 可能因依赖缺失或循环导入而失败
            # 降级到占位数据，保证 API 不直接崩溃，前端可展示友好提示
            logger.warning("DataFetcherManager 未找到，使用占位数据")
            return self._get_placeholder_quote(stock_code)
        except Exception as e:
            # 兜底异常：记录完整堆栈便于排查，返回 None 让上层决定如何展示
            logger.error(f"获取实时行情失败: {e}", exc_info=True)
            return None
    
    def get_history_data(
        self,
        stock_code: str,
        period: str = "daily",
        days: int = 30
    ) -> Dict[str, Any]:
        """
        获取股票历史行情
        
        Args:
            stock_code: 股票代码
            period: K 线周期 (daily/weekly/monthly)
            days: 获取天数
            
        Returns:
            历史行情数据字典
            
        Raises:
            ValueError: 当 period 不是 daily 时抛出（weekly/monthly 暂未实现）
        """
        # 验证 period 参数，只支持 daily
        if period != "daily":
            raise ValueError(
                f"暂不支持 '{period}' 周期，目前仅支持 'daily'。"
                "weekly/monthly 聚合功能将在后续版本实现。"
            )
        
        try:
            # 调用数据获取器获取历史数据
            # DataFetcherManager.get_daily_data 内部会按优先级尝试多个数据源
            # 返回 (DataFrame, source_name) 元组，source_name 用于调试和监控
            from data_provider.base import DataFetcherManager

            manager = DataFetcherManager()
            df, source = manager.get_daily_data(stock_code, days=days)

            if df is None or df.empty:
                # 日线数据为空的可能原因：1) 新股上市不足；2) 数据源全部失败；3) 代码不存在
                logger.warning(f"获取 {stock_code} 历史数据失败")
                return {"stock_code": stock_code, "period": period, "data": []}

            # 从数据源获取股票中文名称，用于前端展示
            stock_name = manager.get_stock_name(stock_code)
            
            # 转换为前端友好的响应格式
            # 遍历 DataFrame 行，把日期格式化为 ISO 字符串，数值字段做安全转换
            # 注意：iterrows() 会返回 (index, Series)，这里忽略 index
            data = []
            for _, row in df.iterrows():
                date_val = row.get("date")
                if hasattr(date_val, "strftime"):
                    # pandas Timestamp 或 datetime 对象，格式化为 YYYY-MM-DD
                    date_str = date_val.strftime("%Y-%m-%d")
                else:
                    # 兜底：直接字符串化
                    date_str = str(date_val)

                # 使用 float() 做安全数值转换，缺失值会被转为 0.0
                # 成交量和成交额可能为 None，需要额外判断避免把 None 转成 0.0
                data.append({
                    "date": date_str,
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": float(row.get("volume", 0)) if row.get("volume") else None,
                    "amount": float(row.get("amount", 0)) if row.get("amount") else None,
                    "change_percent": float(row.get("pct_chg", 0)) if row.get("pct_chg") else None,
                })
            
            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "period": period,
                "data": data,
            }
            
        except ImportError:
            # 当 data_provider 包不可用时降级处理
            # 常见于测试环境或依赖未完全安装的场景
            logger.warning("DataFetcherManager 未找到，返回空数据")
            return {"stock_code": stock_code, "period": period, "data": []}
        except Exception as e:
            # 兜底异常：记录完整堆栈便于排查数据源故障
            # 返回空数据结构而非抛异常，避免 API 500 错误影响前端体验
            logger.error(f"获取历史数据失败: {e}", exc_info=True)
            return {"stock_code": stock_code, "period": period, "data": []}
    
    def _get_placeholder_quote(self, stock_code: str) -> Dict[str, Any]:
        """
        获取占位行情数据（用于测试）
        
        Args:
            stock_code: 股票代码
            
        Returns:
            占位行情数据
        """
        return {
            "stock_code": stock_code,
            "stock_name": f"股票{stock_code}",
            "current_price": 0.0,
            "change": None,
            "change_percent": None,
            "open": None,
            "high": None,
            "low": None,
            "prev_close": None,
            "volume": None,
            "amount": None,
            "update_time": datetime.now().isoformat(),
        }
