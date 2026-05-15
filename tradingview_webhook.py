from __future__ import annotations

import csv
import json
import os
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv


class TradingViewHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
            record = normalize_payload(payload)
            append_level(record)
        except Exception as exc:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"bad request: {exc}".encode("utf-8"))
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:
        print(f"TradingView webhook: {format % args}")


def normalize_payload(payload: dict[str, object]) -> dict[str, object]:
    secret = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "")
    if secret and str(payload.get("secret", "")) != secret:
        raise ValueError("invalid secret")

    symbol = str(payload.get("symbol", "")).upper().replace("/", "").replace("C:", "")
    kind = str(payload.get("kind", payload.get("type", ""))).lower()
    price = float(payload.get("price", payload.get("level", 0)))
    tests = int(float(payload.get("tests", 1)))
    timeframe = str(payload.get("timeframe", "TradingView"))
    if not symbol:
        raise ValueError("missing symbol")
    if kind not in {"support", "resistance"}:
        raise ValueError("kind must be support or resistance")
    if price <= 0:
        raise ValueError("price must be positive")
    return {
        "received_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "kind": kind,
        "price": price,
        "tests": max(1, tests),
        "timeframe": timeframe,
    }


def append_level(record: dict[str, object]) -> None:
    path = Path(os.getenv("LAYER2_TRADINGVIEW_LEVELS_PATH", "data/tradingview_levels.csv"))
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["received_at", "symbol", "kind", "price", "tests", "timeframe"],
        )
        if not exists:
            writer.writeheader()
        writer.writerow(record)
    print(f"Saved TradingView {record['kind']} {record['symbol']} {record['price']}")


def main() -> int:
    load_dotenv()
    host = os.getenv("TRADINGVIEW_WEBHOOK_HOST", "0.0.0.0")
    port = int(os.getenv("TRADINGVIEW_WEBHOOK_PORT", "8787"))
    server = ThreadingHTTPServer((host, port), TradingViewHandler)
    print(f"TradingView webhook listening on http://{host}:{port}")
    print("Expected JSON: {\"symbol\":\"EURUSD\",\"kind\":\"support\",\"price\":1.0800,\"tests\":3}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
