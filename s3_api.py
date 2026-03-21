"""
s3_api.py — LARIANT S3 Data Service (Phase 2a)
================================================
Standalone FastAPI service that reads `positions.json` and `trades.jsonl`
from AWS S3 and returns structured JSON for the LARIANT frontend.

Environment variables (set in .env or Railway service):
  AWS_ACCESS_KEY_ID        — IAM key with s3:GetObject on the bucket
  AWS_SECRET_ACCESS_KEY    — IAM secret
  AWS_DEFAULT_REGION       — e.g. us-east-1
  S3_BUCKET                — bucket name, e.g. lariant-data
  S3_POSITIONS_KEY         — (optional) default: positions.json
  S3_TRADES_KEY            — (optional) default: trades.jsonl
  PORT                     — (optional) default: 8000

Run locally:
  pip install fastapi uvicorn boto3 python-dotenv
  uvicorn s3_api:app --reload --port 8000
"""

import os
import json
import logging
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional — not needed in production

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("s3_api")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="LARIANT S3 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to Railway domain in production
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── S3 config ─────────────────────────────────────────────────────────────────
S3_BUCKET        = os.environ.get("S3_BUCKET", "")
S3_POSITIONS_KEY = os.environ.get("S3_POSITIONS_KEY", "positions.json")
S3_TRADES_KEY    = os.environ.get("S3_TRADES_KEY", "trades.jsonl")


def _s3_client() -> Any:
    """
    Build a boto3 S3 client from environment variables.
    Credentials are read EXCLUSIVELY from os.environ — never from ~/.aws.
    """
    return boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def _fetch_object(client: Any, key: str) -> bytes:
    """Download an S3 object and return its raw bytes."""
    try:
        resp = client.get_object(Bucket=S3_BUCKET, Key=key)
        return resp["Body"].read()
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        raise HTTPException(status_code=502, detail=f"S3 ClientError [{key}]: {code}") from exc
    except BotoCoreError as exc:
        raise HTTPException(status_code=502, detail=f"S3 BotoCoreError [{key}]: {exc}") from exc


def _parse_positions(raw: bytes) -> list[dict]:
    """Parse positions.json — array of position objects."""
    try:
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, list) else data.get("positions", [])
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("positions.json parse error: %s", exc)
        return []


def _parse_trades(raw: bytes) -> list[dict]:
    """
    Parse trades.jsonl — one JSON object per line.
    Tolerates blank lines and malformed records.
    """
    trades = []
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            trades.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return trades


def _latest_sigma(trades: list[dict]) -> dict[str, float]:
    """
    Walk trades in chronological order and capture the last `sigma_used`
    seen for each city key ('LON', 'SEO').  Last write wins — assumes the
    list is sorted oldest→newest (tempbot appends in order).
    """
    sigma: dict[str, float] = {}
    for t in trades:
        city  = str(t.get("city", "")).strip().upper()
        value = t.get("sigma_used")
        if city and value is not None:
            try:
                sigma[city] = float(value)
            except (TypeError, ValueError):
                pass
    return sigma


def _map_position(p: dict) -> dict:
    """
    Normalise a raw S3 position record to the schema the frontend expects.

    Accepted S3 field names (tempbot.py may use any of these):
      question / market / title          → title
      token_id / asset_id / market_id    → id
      outcome / side                     → outcome  ("YES" | "NO")
      size                               → size      (contracts)
      entry_price / avg_price            → entry_price
      current_price                      → current_price
      cost_basis / invested              → cost_basis  (total $ in)
      current_value / value              → current_value
      edge_entry / edge / model_edge     → edge_entry  (optional)
    """
    def f(v, default=0.0):
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    title        = p.get("question") or p.get("market") or p.get("title") or "Untitled"
    token_id     = str(p.get("token_id") or p.get("asset_id") or p.get("market_id") or id(p))
    outcome      = str(p.get("outcome") or p.get("side") or "YES").upper()
    size         = f(p.get("size"), 0.0)
    entry_price  = f(p.get("entry_price") or p.get("avg_price"), 0.0)
    curr_price   = f(p.get("current_price"), entry_price)     # fallback to entry if stale

    # Cost basis: use explicit field or derive from entry_price × size
    cost_basis   = f(p.get("cost_basis") or p.get("invested"), entry_price * size)
    curr_value   = f(p.get("current_value") or p.get("value"), curr_price * size)

    # Mark-to-Market PnL — signed correctly for both YES and NO legs
    # For YES:  PnL = (current_price − entry_price) × size
    # For NO:   same formula (NO token price moves inversely to YES, but the
    #           bot records the actual NO token price in entry_price/current_price,
    #           so the arithmetic is identical)
    cash_pnl     = curr_value - cost_basis
    pct_pnl      = (cash_pnl / cost_basis * 100) if cost_basis > 0 else 0.0

    # Optional edge
    raw_edge     = p.get("edge_entry") or p.get("edge") or p.get("model_edge")
    edge_entry   = f(raw_edge) if raw_edge is not None else None

    mapped: dict = {
        "id":           token_id,
        "title":        title[:120],
        "size":         size,
        "currentValue": round(curr_value,  6),
        "cashPnl":      round(cash_pnl,    6),
        "percentPnl":   round(pct_pnl,     4),
        "entryPrice":   round(entry_price, 6),
        "currentPrice": round(curr_price,  6),
        "outcome":      outcome,
    }
    if edge_entry is not None:
        mapped["edgeEntry"] = round(edge_entry, 6)

    return mapped


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "bucket": S3_BUCKET or "(not configured)"}


@app.get("/api/s3data")
def get_s3_data():
    """
    Main endpoint consumed by the Next.js dashboard.

    Returns:
      {
        s3Available: bool,
        positions:   Position[],   # mapped to frontend schema
        sigmaLon:    float,
        sigmaSeo:    float,
      }
    """
    if not S3_BUCKET:
        log.warning("S3_BUCKET not set — returning empty payload")
        return {"s3Available": False, "positions": [], "sigmaLon": 2.2, "sigmaSeo": 2.8}

    client = _s3_client()

    # ── positions.json ────────────────────────────────────────────────────────
    positions_raw  = _fetch_object(client, S3_POSITIONS_KEY)
    raw_positions  = _parse_positions(positions_raw)
    positions      = [_map_position(p) for p in raw_positions]

    # ── trades.jsonl ──────────────────────────────────────────────────────────
    trades_raw = _fetch_object(client, S3_TRADES_KEY)
    trades     = _parse_trades(trades_raw)
    sigma      = _latest_sigma(trades)

    log.info(
        "S3 fetch OK — %d positions | sigma LON=%.2f SEO=%.2f",
        len(positions),
        sigma.get("LON", 2.2),
        sigma.get("SEO", 2.8),
    )

    return {
        "s3Available": True,
        "positions":   positions,
        "sigmaLon":    sigma.get("LON", 2.2),
        "sigmaSeo":    sigma.get("SEO", 2.8),
    }


# ── Dev entrypoint ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("s3_api:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=True)
