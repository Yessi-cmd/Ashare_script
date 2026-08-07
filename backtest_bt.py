"""Backtrader-based backtesting module for A-share strategies.

Provides a dual moving-average crossover strategy (双均线金叉/死叉) and
a Cerebro runner wired to the local DailyBar SQLite store via market_data.
"""

from __future__ import annotations

import datetime
import logging
from argparse import ArgumentParser
from typing import Optional

import backtrader as bt
import pandas as pd

from market_data import load_daily_bars

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# A-Share commission scheme
# ---------------------------------------------------------------------------

class AshareCommission(bt.CommInfoBase):
    """A-share commission: 0.03% commission (min 5 CNY) + 0.05% stamp duty on sells."""

    params = (
        ("commission", 0.0003),   # 佣金 万三
        ("stamp_duty", 0.0005),   # 印花税 千分之0.5（仅卖出）
        ("min_commission", 5.0),  # 最低佣金 5 元
        ("stocklike", True),
        ("commtype", bt.CommInfoBase.COMM_PERC),
    )

    def _getcommission(self, size: float, price: float, pseudoexec: bool) -> float:
        trade_value = abs(size) * price
        commission = max(trade_value * self.p.commission, self.p.min_commission)
        if size < 0:  # sell — add stamp duty
            commission += trade_value * self.p.stamp_duty
        return commission


# ---------------------------------------------------------------------------
# Dual moving-average crossover strategy
# ---------------------------------------------------------------------------

class DualMAStrategy(bt.Strategy):
    """双均线金叉/死叉策略.

    Parameters
    ----------
    fast : int  Fast MA period (default 5)
    slow : int  Slow MA period (default 20)
    print_log : bool  Print per-bar log (default False)
    """

    params = (
        ("fast", 5),
        ("slow", 20),
        ("print_log", False),
    )

    def __init__(self) -> None:
        self.fast_ma = bt.indicators.SMA(self.data.close, period=self.params.fast)
        self.slow_ma = bt.indicators.SMA(self.data.close, period=self.params.slow)
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)
        self.order: Optional[bt.Order] = None
        self.trade_count: int = 0

    def log(self, txt: str) -> None:
        if self.params.print_log:
            dt = self.datas[0].datetime.date(0).isoformat()
            logger.info("%s  %s", dt, txt)

    def notify_order(self, order: bt.Order) -> None:
        if order.status in (order.Submitted, order.Accepted):
            return
        if order.status == order.Completed:
            if order.isbuy():
                self.log(f"买入 {order.executed.size}股 @ {order.executed.price:.2f}")
            else:
                self.log(f"卖出 {order.executed.size}股 @ {order.executed.price:.2f}")
            self.trade_count += 1
        elif order.status in (order.Canceled, order.Margin, order.Rejected):
            self.log(f"订单失败: {order.getstatusname()}")
        self.order = None

    def notify_trade(self, trade: bt.Trade) -> None:
        if trade.isclosed:
            self.log(f"交易盈亏: {trade.pnl:.2f}  净盈亏: {trade.pnlcomm:.2f}")

    def next(self) -> None:
        if self.order is not None:
            return  # wait for pending order

        if not self.position:  # no position — look for golden cross
            if self.crossover[0] > 0:
                lots = int(self.broker.get_cash() / self.data.close[0] / 100)
                size = lots * 100
                if size >= 100:
                    self.log(f"金叉信号 — 买入 {size}股")
                    self.order = self.buy(size=size)
        else:  # holding — look for death cross
            if self.crossover[0] < 0:
                self.log(f"死叉信号 — 平仓 {self.position.size}股")
                self.order = self.close()


# ---------------------------------------------------------------------------
# Equity curve analyzer
# ---------------------------------------------------------------------------

class EquityCurve(bt.Analyzer):
    """Records portfolio value at every bar for charting."""

    def __init__(self) -> None:
        super().__init__()
        self._values: list[dict] = []

    def next(self) -> None:
        self._values.append({
            "date": self.datas[0].datetime.date(0).isoformat(),
            "value": round(self.strategy.broker.getvalue(), 2),
        })

    def get_analysis(self) -> dict:
        return {"values": self._values}


# ---------------------------------------------------------------------------
# Cerebro runner
# ---------------------------------------------------------------------------

def run_backtest(
    stock_code: str,
    fast: int = 5,
    slow: int = 20,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    initial_cash: float = 100_000.0,
    plot: bool = False,
    adjust: str = "qfq",
) -> bt.Result:
    """Run a dual-MA backtest on a single stock.

    Parameters
    ----------
    stock_code : str
        6-digit A-share code, e.g. "600519".
    fast : int
        Fast MA period.
    slow : int
        Slow MA period.
    start_date : str or None
        ISO start date, e.g. "2024-01-01".
    end_date : str or None
        ISO end date, e.g. "2025-12-31".
    initial_cash : float
        Starting capital in CNY.
    plot : bool
        If True, open the Backtrader plot window after the run.
    adjust : str
        Adjustment mode: "qfq" (前复权, default) or "hfq" (后复权).

    Returns
    -------
    bt.Result
        The Cerebro result record.
    """
    # 1. Load data from local SQLite
    sd = datetime.date.fromisoformat(start_date) if start_date else None
    ed = datetime.date.fromisoformat(end_date) if end_date else None

    df = load_daily_bars(stock_code, start_date=sd, end_date=ed, adjust=adjust)
    if df.empty:
        raise ValueError(f"本地无 {stock_code} 的日线数据，请先执行 python research.py sync {stock_code}")

    # Convert to Backtrader feed
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df.set_index("trade_date", inplace=True)
    # Backtrader PandasData expects columns: datetime, open, high, low, close, volume, openinterest
    df["openinterest"] = 0.0
    data = bt.feeds.PandasData(dataname=df)

    # 2. Set up Cerebro
    cerebro = bt.Cerebro()
    cerebro.adddata(data)
    cerebro.addstrategy(DualMAStrategy, fast=fast, slow=slow, print_log=True)
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.addcommissioninfo(AshareCommission())

    # A-share slippage: 0.1% per trade
    cerebro.broker.set_slippage_perc(perc=0.001)

    # 3. Add analyzers
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.02)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.VWR, _name="vwr")  # Variability-Weighted Return
    cerebro.addanalyzer(EquityCurve, _name="equity")

    # 4. Run
    start_value = cerebro.broker.getvalue()
    logger.info("起始资金: %.2f", start_value)

    results = cerebro.run()
    strat = results[0]

    end_value = cerebro.broker.getvalue()
    total_return_pct = (end_value / start_value - 1) * 100

    # 5. Print results
    print("\n" + "=" * 60)
    print(f"  Backtrader 双均线回测 — {stock_code}")
    print("=" * 60)
    print(f"  策略参数        快线={fast}日  慢线={slow}日")
    print(f"  回测区间        {df.index[0].date()} → {df.index[-1].date()}")
    print(f"  起始资金        {start_value:,.2f} 元")
    print(f"  最终资金        {end_value:,.2f} 元")
    print(f"  总收益率        {total_return_pct:+.2f}%")
    print("-" * 60)

    # Sharpe
    sharpe = strat.analyzers.sharpe.get_analysis()
    sharpe_ratio = sharpe.get("sharperatio")
    print(f"  夏普比率        {sharpe_ratio:.3f}" if sharpe_ratio is not None else "  夏普比率        N/A")

    # Drawdown
    dd = strat.analyzers.drawdown.get_analysis()
    print(f"  最大回撤        {dd.max.drawdown:.2f}%")
    print(f"  最大回撤时长    {dd.max.len} 天")

    # Trade stats
    trade_analysis = strat.analyzers.trades.get_analysis()
    total_closed = trade_analysis.get("total", {}).get("closed", 0)

    if total_closed == 0:
        avg_price = float(df["close"].iloc[-1])
        min_cash = avg_price * 100
        if initial_cash < min_cash:
            print(f"  ⚠ 无交易：起始资金 {initial_cash:,.0f} 不足以买入一手 "
                  f"(最新价≈{avg_price:.2f}，一手需 {min_cash:,.0f})")
        else:
            print("  ⚠ 无交易：回测区间内未触发任何金叉信号")
        print("=" * 60)
        return results[0]

    won = trade_analysis.get("won", {}).get("total", 0)
    lost = trade_analysis.get("lost", {}).get("total", 0)
    win_rate = won / total_closed * 100 if total_closed else 0.0
    print(f"  总交易次数      {total_closed}")
    print(f"  胜率            {win_rate:.1f}% ({won}赢/{lost}输)")

    # P&L
    pnl = trade_analysis.get("pnl", {})
    gross_total = pnl.get("gross", {}).get("total", 0.0)
    net_total = pnl.get("net", {}).get("total", 0.0)
    print(f"  总毛利          {gross_total:,.2f} 元")
    print(f"  总净利          {net_total:,.2f} 元")
    print("=" * 60)

    # 6. Plot (optional)
    if plot:
        cerebro.plot(style="candlestick", barup="red", bardown="green",
                     volup="red", voldown="green")

    return results[0]


# ---------------------------------------------------------------------------
# Web-friendly runner — returns a template-ready dict
# ---------------------------------------------------------------------------

def run_backtest_web(
    stock_code: str,
    fast: int = 5,
    slow: int = 20,
    start_date: str = "",
    end_date: str = "",
    initial_cash: float = 100_000.0,
    adjust: str = "qfq",
) -> dict:
    """Run dual-MA backtest and return a dict ready for template rendering.

    Keys returned:
        ok : bool          True on success
        error : str        Error message (only when ok=False)
        stock_code, fast, slow, start_date, end_date, initial_cash, adjust
        start_value, end_value, total_return_pct
        sharpe_ratio, max_drawdown, max_drawdown_len
        total_trades, won, lost, win_rate
        gross_profit, net_profit
        equity_curve : list[dict]  [{"date": "YYYY-MM-DD", "value": float}, …]
        first_date, last_date
    """
    base: dict = {
        "ok": False,
        "error": "",
        "stock_code": stock_code,
        "fast": fast,
        "slow": slow,
        "start_date": start_date,
        "end_date": end_date,
        "initial_cash": initial_cash,
        "adjust": adjust,
        "start_value": 0.0,
        "end_value": 0.0,
        "total_return_pct": 0.0,
        "sharpe_ratio": None,
        "max_drawdown": 0.0,
        "max_drawdown_len": 0,
        "total_trades": 0,
        "won": 0,
        "lost": 0,
        "win_rate": 0.0,
        "gross_profit": 0.0,
        "net_profit": 0.0,
        "equity_curve": [],
        "first_date": "",
        "last_date": "",
    }

    sd = datetime.date.fromisoformat(start_date) if start_date else None
    ed = datetime.date.fromisoformat(end_date) if end_date else None

    try:
        df = load_daily_bars(stock_code, start_date=sd, end_date=ed, adjust=adjust)
    except Exception as exc:
        base["error"] = f"加载日线数据失败: {exc}"
        return base

    if df.empty:
        base["error"] = f"本地无 {stock_code} 的日线数据，请先执行 python research.py sync {stock_code}"
        return base

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df.set_index("trade_date", inplace=True)
    df["openinterest"] = 0.0
    data = bt.feeds.PandasData(dataname=df)

    base["first_date"] = str(df.index[0].date())
    base["last_date"] = str(df.index[-1].date())

    cerebro = bt.Cerebro()
    cerebro.adddata(data)
    cerebro.addstrategy(DualMAStrategy, fast=fast, slow=slow, print_log=False)
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.addcommissioninfo(AshareCommission())
    cerebro.broker.set_slippage_perc(perc=0.001)
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.02)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(EquityCurve, _name="equity")

    base["start_value"] = round(cerebro.broker.getvalue(), 2)

    try:
        results = cerebro.run()
    except Exception as exc:
        base["error"] = f"回测运行失败: {exc}"
        return base

    strat = results[0]
    base["end_value"] = round(cerebro.broker.getvalue(), 2)
    if base["start_value"] > 0:
        base["total_return_pct"] = round(
            (base["end_value"] - base["start_value"]) / base["start_value"] * 100, 2
        )

    # Equity curve
    equity = strat.analyzers.equity.get_analysis()
    base["equity_curve"] = equity.get("values", [])

    # Sharpe
    sharpe = strat.analyzers.sharpe.get_analysis()
    sr = sharpe.get("sharperatio")
    if sr is not None:
        base["sharpe_ratio"] = round(float(sr), 3)

    # Drawdown
    dd = strat.analyzers.drawdown.get_analysis()
    base["max_drawdown"] = round(dd.max.drawdown, 2)
    base["max_drawdown_len"] = dd.max.len

    # Trades
    trade_analysis = strat.analyzers.trades.get_analysis()
    total_closed = trade_analysis.get("total", {}).get("closed", 0)
    base["total_trades"] = total_closed

    if total_closed > 0:
        won = trade_analysis.get("won", {}).get("total", 0)
        lost = trade_analysis.get("lost", {}).get("total", 0)
        base["won"] = won
        base["lost"] = lost
        base["win_rate"] = round(won / total_closed * 100, 1)
        pnl = trade_analysis.get("pnl", {})
        base["gross_profit"] = round(pnl.get("gross", {}).get("total", 0.0), 2)
        base["net_profit"] = round(pnl.get("net", {}).get("total", 0.0), 2)
    else:
        avg_price = float(df["close"].iloc[-1])
        min_cash = avg_price * 100
        if initial_cash < min_cash:
            base["error"] = (
                f"起始资金 {initial_cash:,.0f} 元不足以买入一手 "
                f"(最新价≈{avg_price:.2f}，一手需 {min_cash:,.0f} 元)"
            )
        else:
            base["error"] = "回测区间内未触发任何金叉信号"

    base["ok"] = True
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = ArgumentParser(description="Backtrader 双均线回测")
    parser.add_argument("stock_code", help="股票代码，如 600519")
    parser.add_argument("--fast", type=int, default=5, help="快线周期 (默认 5)")
    parser.add_argument("--slow", type=int, default=20, help="慢线周期 (默认 20)")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--cash", type=float, default=100_000.0, help="起始资金 (默认 100000)")
    parser.add_argument("--adjust", default="qfq", choices=("qfq", "hfq"), help="复权方式")
    parser.add_argument("--plot", action="store_true", help="显示回测图表")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

    run_backtest(
        stock_code=args.stock_code,
        fast=args.fast,
        slow=args.slow,
        start_date=args.start,
        end_date=args.end,
        initial_cash=args.cash,
        plot=args.plot,
        adjust=args.adjust,
    )


if __name__ == "__main__":
    main()
