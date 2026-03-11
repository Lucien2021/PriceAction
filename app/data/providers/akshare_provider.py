from __future__ import annotations

import datetime as _dt
from datetime import datetime, date
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
        kwargs = {
            "symbol": symbol.code,
            "period": period,
            "adjust": adjust,
            "start_date": start_date or "20100101",
            "end_date": end_date or datetime.now().strftime("%Y%m%d"),
        }

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
        if df.empty:
            return []

        cols = df.columns.tolist()
        ts_col = cols[0]

        ohlcv = AKShareProvider._detect_ohlcv_columns(df)

        candles: List[Candle] = []
        for _, row in df.iterrows():
            ts = AKShareProvider._parse_timestamp(row[ts_col], daily)
            candles.append(Candle(
                timestamp=ts,
                open=float(row[ohlcv["open"]]),
                high=float(row[ohlcv["high"]]),
                low=float(row[ohlcv["low"]]),
                close=float(row[ohlcv["close"]]),
                volume=float(row[ohlcv["volume"]] or 0),
                turnover=float(row.get(ohlcv.get("turnover", ""), 0) or 0),
            ))
        return candles

    @staticmethod
    def _parse_timestamp(ts_raw, daily: bool) -> datetime:
        if isinstance(ts_raw, datetime):
            return ts_raw
        if isinstance(ts_raw, date):
            return datetime(ts_raw.year, ts_raw.month, ts_raw.day)
        if isinstance(ts_raw, pd.Timestamp):
            return ts_raw.to_pydatetime()
        if isinstance(ts_raw, str):
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(ts_raw, fmt)
                except ValueError:
                    continue
            return pd.Timestamp(ts_raw).to_pydatetime()
        return datetime.now()

    @staticmethod
    def _detect_ohlcv_columns(df: pd.DataFrame) -> dict:
        cols = df.columns.tolist()
        result = {}

        cn_map = {
            "open": ["开盘", "open", "Open"],
            "high": ["最高", "high", "High"],
            "low": ["最低", "low", "Low"],
            "close": ["收盘", "close", "Close"],
            "volume": ["成交量", "volume", "Volume", "vol"],
            "turnover": ["成交额", "turnover", "amount"],
        }

        for key, candidates in cn_map.items():
            for candidate in candidates:
                if candidate in cols:
                    result[key] = candidate
                    break

        if len(result) < 5:
            num_cols = [c for c in cols if df[c].dtype in ("float64", "int64", "float32")]
            if len(num_cols) >= 5:
                result.setdefault("open", num_cols[0])
                result.setdefault("close", num_cols[1])
                result.setdefault("high", num_cols[2])
                result.setdefault("low", num_cols[3])
                result.setdefault("volume", num_cols[4])
                if len(num_cols) > 5:
                    result.setdefault("turnover", num_cols[5])

        return result
