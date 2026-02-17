#!/usr/bin/env python3
"""
财经新闻推送模块
支持：每日定时推送 + 即时获取
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Callable, Any
import threading

import akshare as ak
import pandas as pd
import feedparser

logger = logging.getLogger(__name__)


# ── API 超时保护 ───────────────────────────────────────────

def call_with_timeout(func: Callable, timeout: int = 5, *args, **kwargs) -> Optional[Any]:
    """
    带超时保护的函数调用
    timeout: 超时时间（秒），默认5秒
    """
    result = [None]
    exception = [None]
    
    def target():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            exception[0] = e
    
    thread = threading.Thread(target=target)
    thread.daemon = True
    thread.start()
    thread.join(timeout)
    
    if thread.is_alive():
        logger.warning(f"{func.__name__} 超时（{timeout}秒），已跳过")
        return None
    
    if exception[0]:
        logger.error(f"{func.__name__} 失败: {exception[0]}")
        return None
    
    return result[0]


# ── RSS 新闻源配置 ───────────────────────────────────────────

RSS_FEEDS = {
    "新浪财经": "https://finance.sina.com.cn/roll/finance_1_index.xml",
    "华尔街见闻": "https://wallstreetcn.com/rss",
    "FT中文网": "https://www.ftchinese.com/rss/news",
}


def fetch_rss_news(feed_url: str, max_items: int = 5) -> List[Dict[str, str]]:
    """
    获取RSS源新闻
    返回: [{"title": "...", "link": "...", "published": "..."}, ...]
    """
    try:
        feed = feedparser.parse(feed_url)
        news_list = []
        
        for entry in feed.entries[:max_items]:
            news_list.append({
                "title": entry.get("title", "无标题"),
                "link": entry.get("link", ""),
                "published": entry.get("published", "")
            })
        
        return news_list
    except Exception as e:
        logger.error(f"RSS获取失败 ({feed_url}): {e}")
        return []


def get_rss_news_summary() -> str:
    """获取RSS新闻摘要（实时更新）"""
    news_items = []
    news_items.append("📰 实时财经资讯")
    news_items.append("=" * 40)
    news_items.append(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    total_news = 0
    for source_name, feed_url in RSS_FEEDS.items():
        logger.info(f"获取 {source_name} RSS...")
        news_list = fetch_rss_news(feed_url, max_items=3)
        
        if news_list:
            news_items.append(f"📌 {source_name}")
            news_items.append("-" * 40)
            for news in news_list:
                title = news["title"]
                if len(title) > 50:
                    title = title[:47] + "..."
                news_items.append(f"• {title}")
            news_items.append("")  # 空行分隔
            total_news += len(news_list)
    
    if total_news == 0:
        news_items.append("⚠️ 暂无新闻更新")
    
    news_items.append("=" * 40)
    return "\n".join(news_items)


def get_morning_news() -> str:
    """
    早间新闻（8:00）
    - 隔夜外盘（带超时保护）
    - 财经快讯
    """
    news_items = []
    news_items.append("📰 早间财经简报")
    news_items.append("=" * 40)
    news_items.append(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    has_content = False
    
    # 1. 东方财富快讯（带超时 + 日期检测）
    logger.info("获取东方财富快讯...")
    df_news = call_with_timeout(ak.stock_news_em, timeout=5)
    if df_news is not None and not df_news.empty:
        news_items.append("📌 最新快讯")
        news_items.append("-" * 40)
        now = datetime.now()
        shown_count = 0
        for idx, row in df_news.head(10).iterrows():
            title = row.get("新闻标题", "")
            time_str = row.get("发布时间", "")
            
            # 日期检测（如果新闻超过3天标注）
            try:
                news_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                days_old = (now - news_time).days
                if days_old > 3:
                    title = f"{title} ⏰历史({days_old}天前)"
            except:
                pass
            
            if len(title) > 60:
                title = title[:57] + "..."
            news_items.append(f"• {title}")
            shown_count += 1
            if shown_count >= 5:
                break
        news_items.append("")
        has_content = True
    
    # 2. 隔夜外盘（美股，带超时 + 数据验证）
    logger.info("获取美股指数...")
    df_us = call_with_timeout(ak.index_us_stock_sina, timeout=5)
    if df_us is not None and not df_us.empty:
        # API不返回名称，硬编码三大指数名称
        us_names = ["道琼斯指数", "纳斯达克", "标普500"]
        
        # 检查数据是否有效（避免全0数据）
        valid_data = False
        us_indices = []
        for i, (idx, row) in enumerate(df_us.head(3).iterrows()):
            name = us_names[i] if i < len(us_names) else "未知指数"
            close = row.get("close", 0)
            # 计算涨跌幅（如果有昨日数据）
            chg_pct = 0.0
            if i > 0 and i < len(df_us):
                prev_close = df_us.iloc[i-1].get("close", close)
                if prev_close > 0:
                    chg_pct = (close - prev_close) / prev_close * 100
            
            if abs(chg_pct) > 0.01:  # 涨跌幅至少0.01%才算有效
                valid_data = True
            
            emoji = "🔴" if chg_pct > 0 else "🟢"  # 红涨绿跌
            us_indices.append(f"{emoji} {name}: {close:.2f} ({chg_pct:+.2f}%)")
        
        if us_indices:  # 只要有数据就显示
            news_items.append("🌍 隔夜外盘")
            news_items.append("-" * 40)
            news_items.extend(us_indices)
            if not valid_data:
                news_items.append("⚠️ 数据可能未更新（休市期间）")
            news_items.append("")
            has_content = True
    
    # 3. 如果API都超时，降级到RSS
    if not has_content:
        logger.info("API超时，使用RSS新闻...")
        for source_name, feed_url in list(RSS_FEEDS.items())[:2]:  # 只取2个源
            news_list = fetch_rss_news(feed_url, max_items=3)
            if news_list:
                news_items.append(f"📌 {source_name}")
                news_items.append("-" * 40)
                for news in news_list:
                    title = news["title"]
                    if len(title) > 50:
                        title = title[:47] + "..."
                    news_items.append(f"• {title}")
                news_items.append("")
    
    news_items.append("=" * 40)
    news_items.append("祝您投资顺利！💰")
    
    return "\n".join(news_items)


def get_evening_news() -> str:
    """
    晚间总结（18:00）
    - A股收盘数据（带超时保护）
    - 资金流向
    - RSS新闻兜底
    """
    news_items = []
    news_items.append("📊 晚间市场总结")
    news_items.append("=" * 40)
    news_items.append(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    has_content = False
    
    # 1. A股主要指数（带超时 + 错误处理）
    try:
        logger.info("获取A股指数...")
        df_index = call_with_timeout(ak.stock_zh_index_spot_em, timeout=5)
        if df_index is not None and not df_index.empty:
            major_indices = df_index[df_index["代码"].isin(["000001", "399001", "399006"])]
            if not major_indices.empty:
                news_items.append("📈 主要指数")
                news_items.append("-" * 40)
                for idx, row in major_indices.iterrows():
                    name = row.get("名称", "")
                    close = row.get("最新价", 0)
                    chg_pct = row.get("涨跌幅", 0)
                    emoji = "🔴" if chg_pct > 0 else "🟢"
                    news_items.append(f"{emoji} {name}: {close:.2f} ({chg_pct:+.2f}%)")
                news_items.append("")
                has_content = True
    except Exception as e:
        logger.error(f"获取指数失败: {e}")
    
    # 2. 涨跌停统计（带超时 + 错误处理）
    try:
        logger.info("获取涨跌停数据...")
        today = datetime.now().strftime('%Y%m%d')
        df_limit = call_with_timeout(ak.stock_zt_pool_em, timeout=5, date=today)
        df_dt = call_with_timeout(ak.stock_dt_pool_em, timeout=5, date=today)
        
        if df_limit is not None or df_dt is not None:
            zt_count = len(df_limit) if df_limit is not None and not df_limit.empty else 0
            dt_count = len(df_dt) if df_dt is not None and not df_dt.empty else 0
            news_items.append(f"📊 涨跌停: 涨停 {zt_count} 只 | 跌停 {dt_count} 只")
            news_items.append("")
            has_content = True
    except Exception as e:
        logger.error(f"获取涨跌停失败: {e}")
    
    # 3. 资金流向（带超时 + 错误处理）
    try:
        logger.info("获取资金流向...")
        df_flow = call_with_timeout(ak.stock_individual_fund_flow_rank, timeout=5, indicator="今日")
        if df_flow is not None and not df_flow.empty:
            news_items.append("💰 资金流入 TOP5")
            news_items.append("-" * 40)
            for idx, row in df_flow.head(5).iterrows():
                name = row.get("名称", "")
                flow = row.get("主力净流入-净额", 0)
                flow_pct = row.get("主力净流入-净占比", 0)
                news_items.append(f"{idx+1}. {name}: {flow/1e8:.2f}亿 ({flow_pct:.1f}%)")
            news_items.append("")
            has_content = True
    except Exception as e:
        logger.error(f"获取资金流向失败: {e}")
    
    # 4. 如果API都超时，降级到RSS
    if not has_content:
        logger.info("API超时，使用RSS新闻...")
        try:
            for source_name, feed_url in list(RSS_FEEDS.items())[:2]:
                news_list = fetch_rss_news(feed_url, max_items=3)
                if news_list:
                    news_items.append(f"📌 {source_name}")
                    news_items.append("-" * 40)
                    for news in news_list:
                        title = news["title"]
                        if len(title) > 50:
                            title = title[:47] + "..."
                        news_items.append(f"• {title}")
                    news_items.append("")
                    has_content = True
        except Exception as e:
            logger.error(f"获取RSS失败: {e}")
    
    # 5. 如果所有数据源都失败
    if not has_content:
        news_items.append("⚠️ 暂无数据更新（可能是休市期间）")
        news_items.append("")
    
    news_items.append("=" * 40)
    news_items.append("感谢关注！明天见 👋")
    
    return "\n".join(news_items)


def get_instant_news() -> str:
    """
    即时新闻（用户主动请求）
    优先使用RSS实时新闻，失败时降级为akshare
    """
    # 优先尝试RSS新闻（实时）
    try:
        rss_news = get_rss_news_summary()
        if rss_news and "暂无新闻更新" not in rss_news:
            return rss_news
    except Exception as e:
        logger.warning(f"RSS新闻获取失败，尝试备用数据源: {e}")
    
    # 降级：使用akshare（可能不是最新）
    news_items = []
    news_items.append("⚡ 即时财经快讯")
    news_items.append("=" * 40)
    news_items.append(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    try:
        # 1. 东方财富快讯（最新10条）
        logger.info("获取东方财富快讯...")
        df_news = ak.stock_news_em()
        if df_news is not None and not df_news.empty:
            news_items.append("📢 最新快讯")
            news_items.append("-" * 40)
            for idx, row in df_news.head(10).iterrows():
                title = row.get("新闻标题", "")
                time_str = row.get("发布时间", "")
                # 限制长度
                if len(title) > 60:
                    title = title[:57] + "..."
                news_items.append(f"• [{time_str}] {title}\n")
        
        # 2. 当前市场状态
        logger.info("获取市场状态...")
        now = datetime.now()
        hour = now.hour
        if 9 <= hour < 15:
            # 交易时段，显示实时指数
            df_index = ak.stock_zh_index_spot_em()
            if df_index is not None and not df_index.empty:
                sh = df_index[df_index["代码"] == "000001"].iloc[0]
                news_items.append("\n📊 上证指数")
                news_items.append(
                    f"  {sh['最新价']:.2f} ({sh['涨跌幅']:+.2f}%)"
                )
        else:
            news_items.append("\n💤 市场已休市")
        
    except Exception as e:
        logger.error(f"获取即时新闻失败: {e}")
        news_items.append("\n⚠️ 数据获取失败，请稍后重试")
    
    news_items.append("\n" + "=" * 40)
    
    return "\n".join(news_items)


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.INFO)
    print(get_instant_news())
