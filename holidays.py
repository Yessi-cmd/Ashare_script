"""
A股行情监控 - 节假日日历模块
内置 2026 年 A股完整休市日历，自动判断是否为交易日。
"""

from datetime import date, timedelta

# ── 2026年 A股休市日历 ──────────────────────────────────────────
# 数据来源: 上交所/深交所公告
# 包含: 法定节假日休市 + 周末
# 注意: 调休上班日（周六/周日补班）不在此列表中，这些日子股市仍然休市

HOLIDAYS_2026 = {
    # 元旦: 1月1日-1月3日
    date(2026, 1, 1),
    date(2026, 1, 2),
    date(2026, 1, 3),

    # 春节: 2月15日-2月23日
    date(2026, 2, 15),
    date(2026, 2, 16),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 2, 20),
    date(2026, 2, 21),
    date(2026, 2, 22),
    date(2026, 2, 23),

    # 清明节: 4月4日-4月6日
    date(2026, 4, 4),
    date(2026, 4, 5),
    date(2026, 4, 6),

    # 劳动节: 5月1日-5月5日
    date(2026, 5, 1),
    date(2026, 5, 2),
    date(2026, 5, 3),
    date(2026, 5, 4),
    date(2026, 5, 5),

    # 端午节: 5月31日-6月2日
    date(2026, 5, 31),
    date(2026, 6, 1),
    date(2026, 6, 2),

    # 中秋节: 9月25日-9月27日
    date(2026, 9, 25),
    date(2026, 9, 26),
    date(2026, 9, 27),

    # 国庆节: 10月1日-10月7日
    date(2026, 10, 1),
    date(2026, 10, 2),
    date(2026, 10, 3),
    date(2026, 10, 4),
    date(2026, 10, 5),
    date(2026, 10, 6),
    date(2026, 10, 7),
}


def is_holiday(d: date = None) -> bool:
    """判断给定日期是否为休市日（节假日）"""
    if d is None:
        d = date.today()
    return d in HOLIDAYS_2026


def is_weekend(d: date = None) -> bool:
    """判断给定日期是否为周末"""
    if d is None:
        d = date.today()
    return d.weekday() >= 5  # 5=周六, 6=周日


def is_trading_day(d: date = None) -> bool:
    """判断给定日期是否为交易日"""
    if d is None:
        d = date.today()
    return not is_weekend(d) and not is_holiday(d)


def get_next_trading_day(d: date = None) -> date:
    """获取下一个交易日"""
    if d is None:
        d = date.today()
    next_day = d + timedelta(days=1)
    while not is_trading_day(next_day):
        next_day += timedelta(days=1)
    return next_day


def get_holiday_name(d: date = None) -> str:
    """获取节假日名称（如果是的话）"""
    if d is None:
        d = date.today()

    if not is_holiday(d):
        return ""

    # 根据日期范围返回节假日名称
    month, day = d.month, d.day
    if month == 1 and day <= 3:
        return "元旦"
    elif month == 2 and 15 <= day <= 23:
        return "春节"
    elif month == 4 and 4 <= day <= 6:
        return "清明节"
    elif month == 5 and 1 <= day <= 5:
        return "劳动节"
    elif (month == 5 and day == 31) or (month == 6 and day <= 2):
        return "端午节"
    elif month == 9 and 25 <= day <= 27:
        return "中秋节"
    elif month == 10 and 1 <= day <= 7:
        return "国庆节"
    return "节假日"


if __name__ == "__main__":
    today = date.today()
    print(f"今天: {today} ({'交易日' if is_trading_day(today) else '休市'})")

    if is_holiday(today):
        name = get_holiday_name(today)
        print(f"节假日: {name}")

    if is_weekend(today):
        print("今天是周末")

    if not is_trading_day(today):
        next_td = get_next_trading_day(today)
        print(f"下一个交易日: {next_td}")
