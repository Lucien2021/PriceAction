from __future__ import annotations

import logging
import time
from datetime import datetime, date
from typing import List, Optional

import requests as _requests
from requests.adapters import HTTPAdapter as _HTTPAdapter
from urllib3.util.retry import Retry as _Retry

# 东方财富接口：无浏览器头、走系统坏代理、单次区间过长时常见 RemoteDisconnected / ProxyError。
# 在 import akshare 之前打补丁，使 akshare 内建的 requests.get 也生效。
_EASTMONEY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_http_retry = _Retry(
    total=5,
    connect=5,
    read=5,
    backoff_factor=0.8,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"],
)
_orig_session_init = _requests.Session.__init__

def _patched_session_init(self, *args, **kwargs):
    _orig_session_init(self, *args, **kwargs)
    self.trust_env = False
    self.headers.update(_EASTMONEY_HEADERS)
    adapter = _HTTPAdapter(
        max_retries=_http_retry,
        pool_connections=8,
        pool_maxsize=16,
    )
    self.mount("https://", adapter)
    self.mount("http://", adapter)

_requests.Session.__init__ = _patched_session_init

_em_session: _requests.Session | None = None
_orig_requests_get = _requests.get


def _eastmoney_session() -> _requests.Session:
    global _em_session
    if _em_session is None:
        _em_session = _requests.Session()
    return _em_session


def _patched_requests_get(url, params=None, **kwargs):
    u = url if isinstance(url, str) else str(url)
    if "eastmoney.com" in u:
        # 注意：不能把 kline 的 push2his 换成 push2——push2 上同路径常返回空 klines。
        kwargs.setdefault("timeout", 60)
        return _eastmoney_session().get(u, params=params, **kwargs)
    return _orig_requests_get(url, params=params, **kwargs)


_requests.get = _patched_requests_get

import akshare as ak
import pandas as pd

from app.domain.candle import Candle, Symbol, Timeframe, MarketType

log = logging.getLogger(__name__)

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

_MAX_RETRIES = 5
_BASE_DELAY = 2.0
_REQUEST_INTERVAL = 2.0
# 东方财富连续失败时尽快切腾讯，避免单次请求卡太久。
_EM_RETRIES = 3
_EM_BASE_DELAY = 1.5
# 日线单次拉取过长区间时，东方财富端容易直接掐连接；按年拆分可明显降低失败率。
_CHUNK_MAX_DAYS = 366


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


def _stock_zh_a_hist_chunked(
    symbol: str,
    period: str,
    adjust: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    sd = start_date.replace("-", "").replace(" ", "")[:8]
    ed = end_date.replace("-", "").replace(" ", "")[:8]
    t0 = datetime.strptime(sd, "%Y%m%d")
    t1 = datetime.strptime(ed, "%Y%m%d")
    if t0 > t1:
        return pd.DataFrame()

    def _one(b: str, e: str) -> pd.DataFrame:
        return _retry(
            lambda: ak.stock_zh_a_hist(
                symbol=symbol,
                period=period,
                adjust=adjust,
                start_date=b,
                end_date=e,
                timeout=60,
            ),
            retries=_EM_RETRIES,
            base_delay=_EM_BASE_DELAY,
        )

    if (t1 - t0).days <= _CHUNK_MAX_DAYS:
        return _one(sd, ed)

    frames: List[pd.DataFrame] = []
    for i, year in enumerate(range(t0.year, t1.year + 1)):
        if i > 0:
            time.sleep(_REQUEST_INTERVAL)
        beg = max(t0, datetime(year, 1, 1))
        end = min(t1, datetime(year, 12, 31))
        sub = _one(beg.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        if not sub.empty:
            frames.append(sub)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    if "日期" in out.columns:
        out = out.drop_duplicates(subset=["日期"], keep="last")
    return out.sort_values("日期", ignore_index=True, kind="mergesort")


def _symbol_to_tx(code: str) -> str:
    return f"sh{code}" if code.startswith("6") else f"sz{code}"


def _resample_tx_daily_to_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """将腾讯日线聚合成周线/月线（东方财富不可用时）。"""
    if df.empty or period == "daily":
        return df
    d = df.copy()
    d["__dt"] = pd.to_datetime(d["日期"])
    d = d.sort_values("__dt").set_index("__dt")
    rule = "W-FRI" if period == "weekly" else "ME"
    agg = d.resample(rule).agg({
        "开盘": "first",
        "最高": "max",
        "最低": "min",
        "收盘": "last",
        "成交量": "sum",
        "成交额": "sum",
    }).dropna(how="any", subset=["开盘"])
    out = agg.reset_index()
    out["日期"] = out["__dt"].dt.date
    out.drop(columns=["__dt"], inplace=True)
    code = df["股票代码"].iloc[0] if len(df) else ""
    out["股票代码"] = code
    for col in ("振幅", "涨跌幅", "涨跌额", "换手率"):
        out[col] = 0.0
    return out


def _daily_via_tencent(
    symbol_code: str,
    period: str,
    adjust: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    sd = start_date.replace("-", "").replace(" ", "")[:8]
    ed = end_date.replace("-", "").replace(" ", "")[:8]
    tx_sym = _symbol_to_tx(symbol_code)
    df = _retry(
        lambda: ak.stock_zh_a_hist_tx(
            symbol=tx_sym,
            start_date=sd,
            end_date=ed,
            adjust=adjust,
            timeout=60,
        )
    )
    if df.empty:
        return df
    out = df.rename(columns={
        "date": "日期",
        "open": "开盘",
        "close": "收盘",
        "high": "最高",
        "low": "最低",
        "amount": "成交量",
    })
    out["成交额"] = 0.0
    for col in ("振幅", "涨跌幅", "涨跌额", "换手率"):
        out[col] = 0.0
    out["股票代码"] = symbol_code
    if period == "daily":
        return out
    return _resample_tx_daily_to_period(out, period)


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

    def list_a_share_codes(self) -> List[str]:
        try:
            df = ak.stock_info_a_code_name()
        except Exception:
            return []
        out: List[str] = []
        for raw in df["code"].astype(str):
            c = raw.strip()
            if c.isdigit():
                c = c.zfill(6)
            out.append(c)
        return out

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
        try:
            df = _stock_zh_a_hist_chunked(
                symbol=kwargs["symbol"],
                period=kwargs["period"],
                adjust=kwargs["adjust"],
                start_date=kwargs["start_date"],
                end_date=kwargs["end_date"],
            )
        except Exception as exc:
            log.warning("东方财富日线不可用，改用腾讯证券: %s", exc)
            df = _daily_via_tencent(
                kwargs["symbol"],
                kwargs["period"],
                kwargs["adjust"],
                kwargs["start_date"],
                kwargs["end_date"],
            )
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
