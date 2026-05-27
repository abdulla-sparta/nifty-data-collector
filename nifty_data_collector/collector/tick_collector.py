"""
collector/tick_collector.py — Upstox V3 WebSocket (FIXED)

ROOT CAUSE of syms=0:
  1. Auth: Current code fetches a redirect URL. The V3 SDK connects directly to
     wss://api.upstox.com/v3/feed/market-data-feed with an Authorization header.
     The redirect URL approach works for opening the connection but the server
     still expects the header on the actual WS handshake.

  2. Subscribe opcode: The V3 protocol requires the subscribe JSON to be sent as
     BINARY (OPCODE_BINARY), not as text. Sending it as text is silently ignored
     by the server — the subscription never registers.

  3. Message encoding: V3 sends Protobuf binary frames, NOT JSON.
     The old code did message.decode('utf-8') + json.loads() on binary protobuf,
     which always raised an exception caught silently at DEBUG level — so every
     single tick was discarded without a visible error.

FIX: Use Authorization header on fixed WS URL, send subscribe as binary,
     decode messages with MarketDataFeedV3_pb2 + json_format.MessageToDict().
"""

import ssl, json, time, logging, threading
import websocket
import requests
from datetime import datetime
import pytz, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import WATCHLIST, NIFTY_INDEX_KEY
from collector.tick_buffer import TickBuffer
from utils.token_manager import get_access_token

# Protobuf decoder — bundled with upstox-python-sdk (already in requirements via pip)
from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as pb
from google.protobuf import json_format

logger  = logging.getLogger(__name__)
IST     = pytz.timezone("Asia/Kolkata")
ALL_KEYS = list(WATCHLIST.values()) + [NIFTY_INDEX_KEY]

KEY_TO_SYM = {v: k for k, v in WATCHLIST.items()}
KEY_TO_SYM[NIFTY_INDEX_KEY] = "NIFTY"

WS_URL = "wss://api.upstox.com/v3/feed/market-data-feed"


class TickCollector:
    def __init__(self, buffer: TickBuffer):
        self.buffer   = buffer
        self.ws       = None
        self._running = False
        self._thread  = None

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self.ws:
            self.ws.close()

    def _loop(self):
        while self._running:
            try:
                token = get_access_token()
                self.ws = websocket.WebSocketApp(
                    WS_URL,
                    header={"Authorization": f"Bearer {token}"},   # FIX 1: header auth
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=lambda ws, e: logger.error(f"WS error: {e}"),
                    on_close=lambda ws, c, m: logger.warning(f"WS closed: {c}"),
                )
                self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
            except Exception as e:
                logger.error(f"WS connect error: {e}")
            if self._running:
                logger.info("Reconnecting WS in 5s...")
                time.sleep(5)

    def _on_open(self, ws):
        # FIX 2: send subscribe as BINARY opcode, not text
        subscribe_msg = json.dumps({
            "guid": "nc-v1",
            "method": "sub",
            "data": {
                "mode": "full",
                "instrumentKeys": ALL_KEYS,
            },
        }).encode("utf-8")
        ws.send(subscribe_msg, opcode=websocket.ABNF.OPCODE_BINARY)
        logger.info(f"✅ WS connected | mode=full | {len(ALL_KEYS)} instruments")

    def _on_message(self, ws, message):
        # FIX 3: decode Protobuf binary, not JSON
        try:
            decoded = pb.FeedResponse.FromString(message)
            data    = json_format.MessageToDict(decoded)
            feeds   = data.get("feeds", {})
            ts      = datetime.now(IST)

            for key, feed in feeds.items():
                sym = KEY_TO_SYM.get(key)
                if not sym:
                    continue

                # V3 decoded dict structure:
                # feed -> fullFeed -> marketFF (equities) or indexFF (indices)
                full_feed = feed.get("fullFeed", {})
                mktff     = full_feed.get("marketFF") or full_feed.get("indexFF") or {}

                ltpc_data = mktff.get("ltpc", {})
                ltp       = ltpc_data.get("ltp")
                if not ltp:
                    continue

                # ltq and vtt come as strings from MessageToDict (int64 proto fields)
                ltq     = int(ltpc_data.get("ltq", 0) or 0)
                cum_vol = int(mktff.get("vtt", 0) or 0)

                # Bid/ask depth from marketLevel.bidAskQuote list
                baq      = (mktff.get("marketLevel") or {}).get("bidAskQuote", [])
                best_bid = float(baq[0].get("bidP", 0)) if baq else None
                best_ask = float(baq[0].get("askP", 0)) if baq else None

                # Side inference
                is_buy = None
                if best_ask and best_bid:
                    if float(ltp) >= best_ask:
                        is_buy = True
                    elif float(ltp) <= best_bid:
                        is_buy = False

                self.buffer.add_tick(
                    symbol  = sym,
                    ltp     = float(ltp),
                    ts      = ts,
                    is_buy  = is_buy,
                    ltq     = ltq,
                    cum_vol = cum_vol,
                )

        except Exception as e:
            logger.warning(f"Message parse error: {e}")
