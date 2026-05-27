"""
collector/tick_collector.py — Upstox V3 WebSocket
CHANGED: mode switched from 'ltpc' to 'full' to capture traded volume
"""

import ssl, json, time, logging, threading, websocket, requests
from datetime import datetime
import pytz, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import WATCHLIST, NIFTY_INDEX_KEY
from collector.tick_buffer import TickBuffer
from utils.token_manager import get_access_token

logger = logging.getLogger(__name__)
IST    = pytz.timezone("Asia/Kolkata")
ALL_KEYS = list(WATCHLIST.values()) + [NIFTY_INDEX_KEY]

KEY_TO_SYM = {v: k for k, v in WATCHLIST.items()}
KEY_TO_SYM[NIFTY_INDEX_KEY] = "NIFTY"


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

    def _get_ws_url(self) -> str:
        token = get_access_token()
        r = requests.get(
            "https://api.upstox.com/v3/feed/market-data-feed/authorize",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if r.status_code != 200:
            raise RuntimeError(f"WS auth failed: {r.text}")
        return r.json()["data"]["authorizedRedirectUri"]

    def _loop(self):
        while self._running:
            try:
                url = self._get_ws_url()
                self.ws = websocket.WebSocketApp(
                    url,
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
        ws.send(json.dumps({
            "guid": "nc-v1",
            "method": "sub",
            "data": {
                "mode": "full",          # CHANGED: was 'ltpc' — full mode includes volume
                "instrumentKeys": ALL_KEYS,
            },
        }))
        logger.info(f"✅ WS connected | mode=full | {len(ALL_KEYS)} instruments")

    def _on_message(self, ws, message):
        try:
            if isinstance(message, bytes):
                message = message.decode("utf-8")
            feeds = json.loads(message).get("feeds", {})
            ts    = datetime.now(IST)

            for key, feed in feeds.items():
                sym = KEY_TO_SYM.get(key)
                if not sym:
                    continue

                # full mode — primary data block
                ff = feed.get("ff", {})
                mktff = ff.get("marketFF", ff.get("indexFF", {}))

                ltpc = mktff.get("ltpc", {})
                ltp  = ltpc.get("ltp")
                if not ltp:
                    # fallback to top-level ltpc
                    ltp = (feed.get("ltpc") or {}).get("ltp")
                if not ltp:
                    continue

                # Volume from full mode
                # Upstox full mode provides 'vtt' (volume traded today cumulative)
                # and 'ltq' (last traded quantity per tick)
                vol_data  = mktff.get("marketOHLC", {}).get("ohlc", [{}])
                ltq       = int(mktff.get("ltq", 0) or 0)    # last traded qty this tick
                cum_vol   = int(mktff.get("vtt", 0) or 0)    # cumulative volume today

                # Bid/ask side inference from best bid/ask
                depth = mktff.get("marketDepth", {})
                best_ask = None
                best_bid = None
                if depth:
                    asks = depth.get("ask", [])
                    bids = depth.get("bid", [])
                    best_ask = float(asks[0].get("price", 0)) if asks else None
                    best_bid = float(bids[0].get("price", 0)) if bids else None

                # Determine trade side: ltp >= ask → buy, ltp <= bid → sell
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
            logger.debug(f"Message parse: {e}")
