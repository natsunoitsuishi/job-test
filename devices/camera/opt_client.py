"""OPT 相机客户端。

协议（沿用旧项目现场约定）：
  - read_port：相机长连接主动推送结果，payload_format=json 时为一连串 JSON 文档，text 时为按行文本；
  - trigger_port：每次发指令都新建短连接，发送 start_cmd / stop_cmd / trigger_cmd（原样 ASCII，无结尾符）后关闭。
JSON 字段名容忍：code/barcode/data/text/content/value 及其列表形式；没有任何码字段的报文视为状态报文忽略。
"""
from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import re
from typing import Optional

from config.schema import CameraConfig
from devices.common import EventSink, tune_tcp_socket
from domain.models import CameraResult
from infra.logging import get_logger, track_extra
from infra.util.clock import SYSTEM_CLOCK, Clock

from .base import BaseCameraClient, CameraError

logger = get_logger("camera")

_NO_READ_TOKENS = {"", "noread", "no read", "no_read", "no-read", "error", "fail", "failed",
                   "null", "none", "ng", "?"}
_SCALAR_KEYS = ("code", "barcode", "data", "text", "content", "value")
_LIST_KEYS = ("codes", "barcodes", "codelist", "datalist", "values", "results", "result")
_CODE_KEYS = _SCALAR_KEYS + _LIST_KEYS
_SYM_KEYS = ("symbology", "codetype", "type", "symbol", "format")
_JSON_START = re.compile(r"[{\[]")
_LINE_SPLIT = re.compile(r"[\r\n\x00]+")


def _is_valid_code(code: str) -> bool:
    return code.strip().lower() not in _NO_READ_TOKENS


def _norm_key(k: object) -> str:
    return str(k).lower().replace("_", "").replace("-", "")


def _find(d: dict) -> Optional[list[tuple[str, str]]]:
    lower = {_norm_key(k): v for k, v in d.items()}
    sym = next((str(lower[k]) for k in _SYM_KEYS if lower.get(k) not in (None, "")), "")
    for k in _CODE_KEYS:
        if k not in lower:
            continue
        v = lower[k]
        if k in ("result", "results") and not isinstance(v, (dict, list)):
            continue                      # {"result": "OK"} 之类的状态字段不是码
        out: list[tuple[str, str]] = []
        _walk(v, sym, out)
        return out or [("", sym)]
    return None


def _walk(v: object, sym: str, out: list[tuple[str, str]]) -> None:
    if isinstance(v, bool):
        return
    if isinstance(v, str):
        out.append((v.strip(), sym))
    elif isinstance(v, int):
        out.append((str(v), sym))
    elif v is None:
        out.append(("", sym))
    elif isinstance(v, list):
        for x in v:
            _walk(x, sym, out)
    elif isinstance(v, dict):
        found = _find(v)
        if found:
            out.extend(found)


def _extract_codes(obj: object) -> Optional[list[tuple[str, str]]]:
    out: list[tuple[str, str]] = []
    _walk(obj, "", out)
    return out or None


class PayloadFramer:
    """把 TCP 字节流切成完整报文（JSON 文档 / 文本行）。"""
    MAX_BUFFER = 16384

    def __init__(self, fmt: str) -> None:
        self.fmt = fmt
        self._text = ""
        self._dec = json.JSONDecoder()
        self._utf8 = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def feed(self, data: bytes) -> list[bytes]:
        self._text += self._utf8.decode(data)
        return self._split_json() if self.fmt == "json" else self._split_text()

    def _split_text(self) -> list[bytes]:
        parts = _LINE_SPLIT.split(self._text)
        self._text = parts[-1] if len(parts[-1]) <= self.MAX_BUFFER else ""
        return [p.encode("utf-8") for p in parts[:-1] if p.strip()]

    def _split_json(self) -> list[bytes]:
        out: list[bytes] = []
        while self._text:
            m = _JSON_START.search(self._text)
            if m is None:
                self._text = ""
                break
            s, i = self._text, m.start()
            try:
                _, end = self._dec.raw_decode(s, i)
            except json.JSONDecodeError:
                if len(s) - i > self.MAX_BUFFER:      # 长期无法解析：丢弃这个起点继续找
                    self._text = s[i + 1:]
                    continue
                self._text = s[i:]                    # 可能只是半个文档，等待后续数据
                break
            out.append(s[i:end].encode("utf-8"))
            self._text = s[end:]
        return out


def _tcp_local(bind_host: str) -> Optional[tuple[str, int]]:
    return (bind_host, 0) if bind_host else None


class OptCameraClient(BaseCameraClient):
    def __init__(self, cfg: CameraConfig, emit: EventSink, clock: Clock = SYSTEM_CLOCK,
                 bind_host: str = "") -> None:
        super().__init__(cfg, emit, clock, bind_host)
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._framer = PayloadFramer(cfg.payload_format)

    # ------------------------------------------------------------------ 结果推送连接
    async def connect(self) -> None:
        await self.disconnect()
        self._reader, self._writer = await asyncio.open_connection(
            self.cfg.host, self.cfg.read_port, local_addr=_tcp_local(self.bind_host))
        tune_tcp_socket(self._writer)
        self._framer = PayloadFramer(self.cfg.payload_format)

    async def disconnect(self) -> None:
        w, self._reader, self._writer = self._writer, None, None
        if w is not None:
            w.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(w.wait_closed(), 1.0)

    async def fetch_loop(self) -> None:
        reader = self._reader
        if reader is None:
            raise CameraError("未连接")
        while True:
            data = await reader.read(4096)
            if not data:
                raise CameraError("相机关闭了连接")
            recv_ts = self._clock.now()
            for doc in self._framer.feed(data):
                try:
                    results = self.parse_payload(doc, recv_ts)
                except Exception as e:  # noqa: BLE001
                    self.stats["parse_errors"] += 1
                    logger.warning("报文解析失败: %s | %r", e, doc[:120], extra={"camera_id": self.id})
                    continue
                for r in results:
                    self.publish_result(r, recv_ts)

    def parse_payload(self, payload: bytes, recv_ts: Optional[float] = None) -> list[CameraResult]:
        """解析一个报文。有码 → 每个不同码一条成功结果；有码字段但全为空/NoRead → 一条失败结果；
        无码字段的状态报文 → 空列表。result_ts = 接收时刻 − result_latency_ms。"""
        now = self._clock.now() if recv_ts is None else recv_ts
        ts = now - self.cfg.result_latency_ms / 1000.0
        text = payload.decode("utf-8", errors="replace").strip()
        if self.cfg.payload_format == "text":
            raw: dict = {"text": text}
            items: Optional[list[tuple[str, str]]] = [(text, "")]
        else:
            obj = json.loads(text)
            raw = obj if isinstance(obj, dict) else {"data": obj}
            items = _extract_codes(obj)
            if items is None:
                return []
        good: list[tuple[str, str]] = []
        seen: set[str] = set()
        for code, sym in items:
            if _is_valid_code(code) and code not in seen:
                seen.add(code)
                good.append((code, sym))
        if not good:
            return [CameraResult(self.id, None, raw, ts, False, items[0][1] if items else "")]
        return [CameraResult(self.id, c, raw, ts, True, s) for c, s in good]

    # ------------------------------------------------------------------ 指令端口（短连接）
    async def _send_cmd(self, cmd: str) -> None:
        try:
            _r, w = await asyncio.wait_for(
                asyncio.open_connection(self.cfg.host, self.cfg.trigger_port,
                                        local_addr=_tcp_local(self.bind_host)),
                self.cfg.connect_timeout_s)
        except (OSError, asyncio.TimeoutError) as e:
            raise CameraError(f"指令端口连接失败: {type(e).__name__}: {e}") from e
        try:
            w.write(cmd.encode("utf-8"))
            await asyncio.wait_for(w.drain(), self.cfg.connect_timeout_s)
        except (OSError, asyncio.TimeoutError) as e:
            raise CameraError(f"指令发送失败: {type(e).__name__}: {e}") from e
        finally:
            w.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(w.wait_closed(), 0.5)
        logger.debug("指令已发送 %r", cmd, extra={"camera_id": self.id})

    def _require_online(self) -> None:
        if not self.online:
            raise CameraError("相机离线")

    async def _send_start(self) -> None:
        self._require_online()
        await self._send_cmd(self.cfg.start_cmd)

    async def _send_stop(self) -> None:
        self._require_online()
        await self._send_cmd(self.cfg.stop_cmd)

    async def _send_trigger(self, track_id: str) -> None:
        self._require_online()
        await self._send_cmd(self.cfg.trigger_cmd)
        logger.info("trigger sent", **track_extra(track_id, self.id))
