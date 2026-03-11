from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import akshare as ak
import pandas as pd

from app.domain.candle import Candle, Symbol, Timeframe, MarketType


_PERIOD_MAP = {
    Timeframe.M1: "1",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.DAILY: "daily",
    Timeframe.WEEKLY: "weekly",
}


class AKShareProvider:
    """Fetch A-share historical data via AKShare."""

    def fetch_candles(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "qfq",
    ) -> List[Candle]:
        period = _PERIOD_MAP.get(timeframe)
        if period is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        if timeframe.minutes >= Timeframe.DAILY.minutes:
            return self._fetch_daily(symbol, timeframe, start_date, end_date, adjust)
        return self._fetch_intraday(symbol, timeframe, period, start_date, end_date, adjust)

    def search_symbols(self, keyword: str) -> List[Symbol]:
        try:
            df = ak.stock_info_a_code_name()
        except Exception:
            return []
        mask = df["code"].str.contains(keyword) | df["name"].str.contains(keyword, na=False)
        results: List[Symbol] = []
        for _, row in df[mask].head(50).iterrows():
            results.append(Symbol(code=row["code"], name=row["name"], market_type=MarketType.A_SHARE))
        return results

    # ------------------------------------------------------------------

    def _fetch_daily(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start_date: Optional[str],
        end_date: Optional[str],
        adjust: str,
    ) -> List[Candle]:
        period = "daily" if timeframe == Timeframe.DAILY else "weekly"
        kwargs = {"symbol": symbol.code, "period": period, "adjust": adjust}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        df: pd.DataFrame = ak.stock_zh_a_hist(**kwargs)
        return self._df_to_candles(df, daily=True)

    def _fetch_intraday(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        period: str,
        start_date: Optional[str],
        end_date: Optional[str],
        adjust: str,
    ) -> List[Candle]:
        df: pd.DataFrame = ak.stock_zh_a_hist_min_em(
            symbol=symbol.code,
            period=period,
            adjust=adjust,
        )
        if start_date:
            df = df[df.iloc[:, 0] >= start_date]
        if end_date:
            df = df[df.iloc[:, 0] <= end_date]
        return self._df_to_candles(df, daily=False)

    @staticmethod
    def _df_to_candles(df: pd.DataFrame, daily: bool) -> List[Candle]:
        col_map = {
            "日期": "ts", "时间": "ts",
            "开盘": "open", "最高": "high", "最低": "low", "收盘": "close",
            "成交量": "volume", "成交额": "turnover",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        ts_col = df.columns[0]
        candles: List[Candle] = []
        for _, row in df.iterrows():
            ts_raw = row[ts_col]
            if isinstance(ts_raw, str):
                fmt = "%Y-%m-%d" if daily else "%Y-%m-%d %H:%M:%S"
                try:
                    ts = datetime.strptime(ts_raw, fmt)
                except ValueError:
                    ts = pd.Timestamp(ts_raw).to_pydatetime()
            elif isinstance(ts_raw, pd.Timestamp):
                ts = ts_raw.to_pydatetime()
            else:
                ts = datetime.now()

            candles.append(Candle(
                timestamp=ts,
                open=float(row.get("open", row.iloc[1])),
                high=float(row.get("high", row.iloc[2])),
                low=float(row.get("low", row.iloc[3])),
                close=float(row.get("close", row.iloc[4])),
                volume=float(row.get("volume", row.iloc[5]) or 0),
                turnover=float(row.get("turnover", 0) or 0),
            ))
        return candles
