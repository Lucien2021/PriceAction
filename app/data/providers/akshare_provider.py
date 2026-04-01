from __future__ import annotations

import json as _json
import logging
import os
import time
from datetime import datetime, date
from pathlib import Path
from typing import Callable, List, Optional, Set

import requests as _requests
from requests.adapters import HTTPAdapter as _HTTPAdapter
from urllib3.util.retry import Retry as _Retry

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
        kwargs.setdefault("timeout", 60)
        return _eastmoney_session().get(u, params=params, **kwargs)
    return _orig_requests_get(url, params=params, **kwargs)


_requests.get = _patched_requests_get

import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402

from app.domain.candle import Candle, Symbol, Timeframe, MarketType  # noqa: E402

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
_REQUEST_INTERVAL = 1.0
_EM_RETRIES = 3
_EM_BASE_DELAY = 1.5
_CHUNK_MAX_DAYS = 366

# --------------- 股票列表缓存 ---------------
_CACHE_DIR = Path(os.environ.get("PRICE_ACTION_DATA", Path.home() / ".price_action"))
_CODE_CACHE_FILE = _CACHE_DIR / "a_share_codes.json"
_CODE_CACHE_TTL = 7 * 86400  # 7 天


def _retry(fn, *, retries: int = _MAX_RETRIES, base_delay: float = _BASE_DELAY):
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


# --------------- 快速获取 A 股代码（不依赖 AKShare） ---------------

def _fetch_codes_cninfo() -> List[str]:
    """巨潮资讯 JSON API，单次请求 ~2 秒拿到全市场 A 股代码。"""
    url = "http://www.cninfo.com.cn/new/data/szse_stock.json"
    headers = {"User-Agent": _EASTMONEY_HEADERS["User-Agent"], "Accept": "application/json"}
    resp = _requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("stockList", data) if isinstance(data, dict) else data
    codes: List[str] = []
    seen: Set[str] = set()
    for it in items:
        cat = str(it.get("category", ""))
        if "A" not in cat:
            continue
        c = str(it.get("code", "")).strip().zfill(6)
        if len(c) == 6 and c not in seen:
            seen.add(c)
            codes.append(c)
    return codes


def _fetch_codes_em_json() -> List[str]:
    """东方财富 JSON API，分页取全量（每页 5000，约 2 次请求）。"""
    url = "http://push2.eastmoney.com/api/qt/clist/get"
    base_params = {
        "po": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f12",
    }
    headers = {"User-Agent": _EASTMONEY_HEADERS["User-Agent"]}
    codes: List[str] = []
    seen: Set[str] = set()
    pn = 1
    while True:
        params = {**base_params, "pn": str(pn), "pz": "5000"}
        resp = _requests.get(url, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        body = resp.json()
        d = body.get("data")
        if not d:
            break
        diff = d.get("diff", {})
        items = diff.values() if isinstance(diff, dict) else diff
        batch = 0
        for it in items:
            c = str(it.get("f12", "")).strip()
            if c and c not in seen:
                seen.add(c)
                codes.append(c)
                batch += 1
        if batch == 0:
            break
        pn += 1
        if pn > 20:
            break
    return codes


def _load_cached_codes() -> Optional[List[str]]:
    try:
        if not _CODE_CACHE_FILE.exists():
            return None
        age = time.time() - _CODE_CACHE_FILE.stat().st_mtime
        if age > _CODE_CACHE_TTL:
            return None
        data = _json.loads(_CODE_CACHE_FILE.read_text(encoding="utf-8"))
        codes = data.get("codes", [])
        if len(codes) >= 500:
            return codes
    except Exception:
        pass
    return None


def _save_codes_cache(codes: List[str]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CODE_CACHE_FILE.write_text(
            _json.dumps({"ts": time.time(), "count": len(codes), "codes": codes},
                        ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


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


def _codes_from_dataframe(df: Optional[pd.DataFrame]) -> List[str]:
    """从 AKShare 返回的 DataFrame 中提取股票代码。"""
    if df is None or df.empty:
        return []
    code_col = None
    for name in df.columns:
        nstr = str(name)
        if nstr.lower() == "code" or "代码" in nstr:
            code_col = name
            break
    if code_col is None:
        for name in df.columns:
            if "code" in str(name).lower():
                code_col = name
                break
    if code_col is None and len(df.columns) >= 1:
        code_col = df.columns[0]
    out: List[str] = []
    seen: Set[str] = set()
    for raw in df[code_col]:
        s = str(raw).strip()
        if "." in s:
            s = s.split(".")[0]
        digits = "".join(ch for ch in s if ch.isdigit())
        if 4 <= len(digits) <= 6:
            c = digits.zfill(6)
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


class AKShareProvider:
    """Fetch A-share historical data via AKShare."""

    def __init__(self):
        self._last_request_ts: float = 0.0
        self.last_a_share_list_error: str = ""

    def _throttle(self):
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
        codes = self.list_a_share_codes()
        results: List[Symbol] = []
        for c in codes:
            if keyword in c:
                results.append(Symbol(code=c, name=c, market_type=MarketType.A_SHARE))
        if len(results) < 50:
            try:
                df = ak.stock_info_a_code_name()
                mask = df["code"].str.contains(keyword) | df["name"].str.contains(keyword, na=False)
                for _, row in df[mask].head(50).iterrows():
                    results.append(Symbol(code=row["code"], name=row["name"], market_type=MarketType.A_SHARE))
            except Exception:
                pass
        return results[:50]

    def list_a_share_codes(self) -> List[str]:
        """获取全市场 A 股代码。优先级：本地缓存 → 巨潮 → 东财JSON → AKShare。"""
        self.last_a_share_list_error = ""

        cached = _load_cached_codes()
        if cached:
            return cached

        err_parts: List[str] = []

        # ---- 1. 巨潮资讯 JSON（最快，单次 ~2s） ----
        try:
            codes = _fetch_codes_cninfo()
            if len(codes) >= 500:
                _save_codes_cache(codes)
                log.info("巨潮资讯获取 %d 只 A 股代码", len(codes))
                return codes
        except Exception as exc:
            err_parts.append(f"cninfo: {exc}")
            log.warning("巨潮资讯 API 失败: %s", exc)

        # ---- 2. 东方财富 JSON API ----
        try:
            codes = _fetch_codes_em_json()
            if len(codes) >= 500:
                _save_codes_cache(codes)
                log.info("东财JSON获取 %d 只 A 股代码", len(codes))
                return codes
        except Exception as exc:
            err_parts.append(f"em_json: {exc}")
            log.warning("东财 JSON API 失败: %s", exc)

        # ---- 3. AKShare 兜底 ----
        for fn_name in ("stock_zh_a_spot_em", "stock_info_a_code_name"):
            try:
                self._throttle()
                df = getattr(ak, fn_name)()
                if df is not None and not df.empty:
                    codes = _codes_from_dataframe(df)
                    if len(codes) >= 500:
                        _save_codes_cache(codes)
                        log.info("%s 获取 %d 只 A 股代码", fn_name, len(codes))
                        return codes
            except Exception as exc:
                err_parts.append(f"{fn_name}: {exc}")
                log.warning("%s 失败: %s", fn_name, exc)

        # ---- 4. 过期缓存也比没有好 ----
        try:
            if _CODE_CACHE_FILE.exists():
                data = _json.loads(_CODE_CACHE_FILE.read_text(encoding="utf-8"))
                codes = data.get("codes", [])
                if codes:
                    log.info("使用过期缓存 (%d 只)", len(codes))
                    return codes
        except Exception:
            pass

        self.last_a_share_list_error = "；".join(err_parts) if err_parts else "全部接口失败"
        return []

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
