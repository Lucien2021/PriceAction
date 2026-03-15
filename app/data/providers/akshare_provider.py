from __future__ import annotations

import datetime as _dt
import logging
import os
import time
import urllib.request
from datetime import datetime, date
from typing import List, Optional

import requests as _requests
from requests.adapters import HTTPAdapter as _HTTPAdapter
from urllib3.util.retry import Retry as _Retry

import akshare as ak
import pandas as pd

from app.domain.candle import Candle, Symbol, Timeframe, MarketType

log = logging.getLogger(__name__)

# ---- Disable proxy for stock-data API calls ----------------------------
urllib.request.getproxies = lambda: {}
for _k in list(os.environ):
    if _k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
        del os.environ[_k]

# ---- Inject HTTP-level retry into every requests.Session ---------------
# akshare uses requests internally; give each session a retry adapter and
# Connection:close so stale keep-alive sockets don't cause
# RemoteDisconnected / ConnectionAborted errors.
_http_retry = _Retry(
    total=3, backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"],
)
_orig_session_init = _requests.Session.__init__

def _patched_session_init(self, *args, **kwargs):
    _orig_session_init(self, *args, **kwargs)
    adapter = _HTTPAdapter(max_retries=_http_retry)
    self.mount("https://", adapter)
    self.mount("http://", adapter)
    self.headers.update({"Connection": "close"})

_requests.Session.__init__ = _patched_session_init
# ------------------------------------------------------------------------

_PERIOD_MAP = {
    Timeframe.M1: "1",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.DAILY: "daily",
    Timeframe.WEEKLY: "weekly",
    Timeframe.MONTHLY: "monthly",
}

_MAX_RETRIES = 3
_BASE_DELAY = 3.0
_REQUEST_INTERVAL = 2.0


def _retry(fn, *, retries: int = _MAX_RETRIES, base_delay: float = _BASE_DELAY):
    """Call *fn* with exponential-backoff retry on failure."""
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_err = exc
            if attempt < retries:
                wait = base_delay * (2 ** (attempt - 1))
                log.warning("attempt %d/%d failed (%s), retry in %.1fs", attempt, retries, exc, wait)
                time.sleep(wait)
    raise last_err  # type: ignore[misc]


class AKShareProvider:
    """Fetch A-share historical data via AKShare."""

    def __init__(self):
        self._last_request_ts: float = 0.0

    def _throttle(self):
        """Ensure minimum interval between consecutive API calls."""
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < _REQUEST_INTERVAL:
            time.sleep(_REQUEST_INTERVAL - elapsed)
        self._last_request_ts = time.monotonic()

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
        period_map = {Timeframe.DAILY: "daily", Timeframe.WEEKLY: "weekly", Timeframe.MONTHLY: "monthly"}
        period = period_map.get(timeframe, "daily")
        kwargs = {
            "symbol": symbol.code,
            "period": period,
            "adjust": adjust,
            "start_date": start_date or "20100101",
            "end_date": end_date or datetime.now().strftime("%Y%m%d"),
        }

        self._throttle()
        df: pd.DataFrame = _retry(lambda: ak.stock_zh_a_hist(**kwargs))
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
        sd = self._to_intraday_date(start_date or "20100101", is_start=True)
        ed = self._to_intraday_date(end_date or datetime.now().strftime("%Y%m%d"), is_start=False)

        self._throttle()
        df: pd.DataFrame = _retry(lambda: ak.stock_zh_a_hist_min_em(
            symbol=symbol.code,
            start_date=sd,
            end_date=ed,
            period=period,
            adjust=adjust,
        ))
        return self._df_to_candles(df, daily=False)

    @staticmethod
    def _to_intraday_date(d: str, is_start: bool) -> str:
        d = d.replace("-", "").replace(" ", "")[:8]
        formatted = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return f"{formatted} 09:30:00" if is_start else f"{formatted} 15:00:00"

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
            "open": ["开盘", "开盘价", "今开", "open", "Open"],
            "high": ["最高", "最高价", "high", "High"],
            "low": ["最低", "最低价", "low", "Low"],
            "close": ["收盘", "收盘价", "close", "Close"],
            "volume": ["成交量", "volume", "Volume", "vol"],
            "turnover": ["成交额", "turnover", "amount"],
        }

        for key, candidates in cn_map.items():
            for candidate in candidates:
                if candidate in cols:
                    result[key] = candidate
                    break

        if len(result) < 5:
            _skip = {"振幅", "涨跌幅", "涨跌额", "换手率", "股票代码",
                      "最新价", "均价", "代码", "名称", "昨收"}
            num_cols = [
                c for c in cols
                if df[c].dtype in ("float64", "int64", "float32")
                and c not in _skip
            ]
            if len(num_cols) >= 5:
                result.setdefault("open", num_cols[0])
                result.setdefault("close", num_cols[1])
                result.setdefault("high", num_cols[2])
                result.setdefault("low", num_cols[3])
                result.setdefault("volume", num_cols[4])
                if len(num_cols) > 5:
                    result.setdefault("turnover", num_cols[5])

        return result
