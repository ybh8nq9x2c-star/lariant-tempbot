#!/usr/bin/env python3
"""
redeemer_worker.py — Lightweight gasless CTF position redeemer for Polymarket EOA.

DESIGN:
  - Completely separate from trading bot. Zero writes to trading files.
  - Sync-only (requests + time.sleep). No asyncio, no aiohttp, no websockets.
  - Max 1 redemption attempt cycle per GLOBAL_INTERVAL (3600s).
  - Per-condition 24h cooldown on any failure.
  - State persisted in redeemer_state.json (never monitor_state.json).
  - Logs to redeemer.log only.

REDEEM PATH PRIORITY:
  1. py_clob_client.redeem_positions()  ← gasless, proven in bot
  2. Direct relayer POST (ASSUMPTION — see inline notes)

REQUIRED ENV:
  PM_PRIVATE_KEY, PM_FUNDER_KEY (or derived from pk), ENABLE_GASLESS_REDEEM=true
OPTIONAL ENV:
  RELAYER_API_KEY, RELAYER_API_KEY_ADDRESS, PM_API_KEY, PM_API_SECRET, PM_API_PASSPHRASE
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

# ── Constants ─────────────────────────────────────────────────────────────────
WORKER_DIR       = Path(__file__).parent
STATE_FILE       = WORKER_DIR / "redeemer_state.json"
LOG_FILE         = WORKER_DIR / "redeemer.log"
ENABLE_ENV       = "ENABLE_GASLESS_REDEEM"
GLOBAL_INTERVAL  = 3600      # seconds — max 1 attempt cycle per hour
PER_CID_COOLDOWN = 86400     # seconds — 24h per-condition retry cooldown
MAIN_SLEEP       = 60        # seconds — main loop idle sleep
NET_TIMEOUT      = 12        # seconds — all HTTP calls
POLYGON_CHAIN_ID = 137
CTF_CONTRACT     = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Polygon CTF Exchange
USDC_E           = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e collateral
CLOB_API_URL     = "https://clob.polymarket.com"
DATA_API_BASE    = "https://data-api.polymarket.com"
RPC_URL          = "https://polygon-bor-rpc.publicnode.com"
# ASSUMPTION: Polymarket relayer URL — adjust if different
RELAYER_URL      = "https://relayer.polymarket.com"

# ── Logging ───────────────────────────────────────────────────────────────────
_handlers = [logging.FileHandler(str(LOG_FILE)), logging.StreamHandler(sys.stdout)]
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [REDEEMER] %(levelname)s %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("redeemer")


# ── State persistence ─────────────────────────────────────────────────────────
def _load_state() -> dict:
    """Load redeemer_state.json. Returns clean default on any error."""
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                s = json.load(f)
            # Migrate: ensure all keys exist
            s.setdefault("redeemed_cids", [])
            s.setdefault("last_global_attempt", 0.0)
            s.setdefault("cid_last_attempt", {})
            s.setdefault("stats", {"total_redeemed": 0, "total_failed": 0, "total_skipped": 0})
            return s
    except Exception as e:
        log.warning(f"STATE_LOAD_ERR: {e} — starting fresh")
    return {
        "redeemed_cids":      [],
        "last_global_attempt": 0.0,
        "cid_last_attempt":   {},
        "stats":              {"total_redeemed": 0, "total_failed": 0, "total_skipped": 0},
    }


def _save_state(s: dict) -> None:
    """Atomic write via temp file. Never raises."""
    try:
        tmp = str(STATE_FILE) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, str(STATE_FILE))
    except Exception as e:
        log.warning(f"STATE_SAVE_ERR: {e}")


# ── EOA helpers ───────────────────────────────────────────────────────────────
def _derive_eoa(pk: str) -> str:
    """Derive EOA address from private key. Returns '' on failure."""
    try:
        from eth_account import Account
        return Account.from_key(pk).address
    except Exception as e:
        log.warning(f"EOA_DERIVE_ERR: {e}")
        return ""


# ── On-chain guard ────────────────────────────────────────────────────────────
def _payout_denominator(cid: str) -> int:
    """
    Call CTF.payoutDenominator(conditionId) on Polygon.
    Returns  0  → not yet resolved on-chain (UMA pending)
    Returns >0  → resolved, safe to redeem
    Returns -1  → RPC call failed, uncertain — allow attempt
    """
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 8}))
        selector = w3.keccak(text="payoutDenominator(bytes32)").hex()[:10]
        raw_cid = cid[2:] if cid.startswith("0x") else cid
        raw = w3.eth.call({"to": CTF_CONTRACT, "data": selector + raw_cid.zfill(64)})
        denom = int(raw.hex(), 16)
        log.debug(f"PAYOUT_DENOM {cid[:16]}: {denom}")
        return denom
    except Exception as e:
        log.warning(f"PAYOUT_DENOM_ERR {cid[:16]}: {e} — allowing attempt (uncertain)")
        return -1


# ── Data API: fetch redeemable positions ─────────────────────────────────────
def _fetch_redeemable(funder: str) -> List[dict]:
    """
    GET data-api.polymarket.com/positions?user={funder}
    Returns list of positions where redeemable=true.
    """
    try:
        import requests
        r = requests.get(
            f"{DATA_API_BASE}/positions?user={funder}",
            timeout=NET_TIMEOUT,
        )
        if r.status_code != 200:
            log.warning(f"DATA_API_HTTP {r.status_code}")
            return []
        data = r.json()
        if not isinstance(data, list):
            log.warning(f"DATA_API_UNEXPECTED_TYPE {type(data)}")
            return []
        redeemable = [p for p in data if p.get("redeemable", False)]
        log.info(f"DATA_API: {len(data)} positions, {len(redeemable)} redeemable")
        return redeemable
    except Exception as e:
        log.warning(f"FETCH_REDEEMABLE_ERR: {e}")
        return []


def _extract_cid(p: dict) -> str:
    """Try all known conditionId field names. Same logic as trading bot."""
    for key in ("conditionId", "condition_id", "market_condition_id", "marketConditionId"):
        v = p.get(key, "")
        if v:
            return str(v)
    log.warning(f"NO_CONDITION_ID in position keys={list(p.keys())}")
    return ""


# ── Primary redeem: py_clob_client ────────────────────────────────────────────
def _redeem_via_clob(cid: str, pk: str, funder: str) -> bool:
    """
    Gasless redemption via py_clob_client.redeem_positions().
    CLOB API internally routes through Polymarket's relayer — no gas needed.
    This is the same path the trading bot uses and is known to work.
    """
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
        from py_clob_client.constants import POLYGON

        client = ClobClient(CLOB_API_URL, key=pk, chain_id=POLYGON, funder=funder)
        client.set_api_creds(ApiCreds(
            api_key        = os.getenv("PM_API_KEY", ""),
            api_secret     = os.getenv("PM_API_SECRET", ""),
            api_passphrase = os.getenv("PM_API_PASSPHRASE", ""),
        ))

        if not hasattr(client, "redeem_positions"):
            log.warning("CLOB_CLIENT has no redeem_positions method")
            return False

        result = client.redeem_positions(condition_id=cid)
        log.info(f"REDEEM_OK (py_clob) {cid[:16]} result={str(result)[:100]}")
        return True

    except Exception as e:
        log.warning(f"REDEEM_FAIL (py_clob) {cid[:16]}: {e}")
        return False


# ── Secondary redeem: direct relayer ─────────────────────────────────────────
# ASSUMPTION: Polymarket's gasless relayer accepts a JSON payload with
# `{to, data, from}` and an Authorization or x-api-key header.
# The RELAYER_API_KEY_ADDRESS (0x3a6fe25c5320dc81f35f0f4e6be8bdc76bc5e1c7) is
# the trusted relayer address that will submit the on-chain tx.
# Endpoint and payload schema are inferred from common Polymarket/Biconomy patterns.
# Mark all assumptions clearly: verify against official Polymarket docs before relying.
def _redeem_via_relayer(cid: str, pk: str, funder: str) -> bool:
    """
    [ASSUMPTION] Direct gasless relayer submission.
    Uses RELAYER_API_KEY for authentication.
    Polls for terminal state before returning success.
    Only called if py_clob_client path failed.
    """
    relay_key  = os.getenv("RELAYER_API_KEY", "")
    relay_addr = os.getenv("RELAYER_API_KEY_ADDRESS",
                           "0x3a6fe25c5320dc81f35f0f4e6be8bdc76bc5e1c7")
    if not relay_key:
        log.info("RELAYER_SKIP: RELAYER_API_KEY not set")
        return False

    try:
        import requests
        from web3 import Web3
        from eth_account import Account

        w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 8}))
        account = Account.from_key(pk)

        # Build redeemPositions calldata
        # ASSUMPTION: binary market → indexSets=[1, 2] (YES=bit0, NO=bit1)
        # parentCollectionId = bytes32(0) for top-level conditions
        ctf_abi = [{
            "name": "redeemPositions",
            "type": "function",
            "inputs": [
                {"name": "collateralToken",    "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId",        "type": "bytes32"},
                {"name": "indexSets",          "type": "uint256[]"},
            ],
            "outputs": [],
            "stateMutability": "nonpayable",
        }]
        ctf = w3.eth.contract(
            address=w3.to_checksum_address(CTF_CONTRACT),
            abi=ctf_abi,
        )
        cid_bytes32 = bytes.fromhex((cid[2:] if cid.startswith("0x") else cid).zfill(64))
        parent_zero = bytes(32)
        calldata = ctf.encodeABI("redeemPositions", args=[
            w3.to_checksum_address(USDC_E),
            parent_zero,
            cid_bytes32,
            [1, 2],  # ASSUMPTION: binary market index sets
        ])

        # ASSUMPTION: relayer POST payload schema
        payload = {
            "to":   CTF_CONTRACT,
            "data": calldata,
            "from": account.address,
        }
        headers = {
            "Authorization":  f"Bearer {relay_key}",
            "x-api-key":      relay_key,
            "Content-Type":   "application/json",
        }
        # ASSUMPTION: relayer submit endpoint
        submit_url = f"{RELAYER_URL}/relay"
        log.info(f"RELAYER_SUBMIT {cid[:16]} to={submit_url}")
        resp = requests.post(submit_url, json=payload, headers=headers,
                             timeout=NET_TIMEOUT)

        if resp.status_code not in (200, 201, 202):
            log.warning(f"RELAYER_HTTP_{resp.status_code} {resp.text[:120]}")
            return False

        resp_data = resp.json() if resp.text else {}
        # ASSUMPTION: response contains txHash or taskId
        task_id = (resp_data.get("txHash")
                   or resp_data.get("hash")
                   or resp_data.get("taskId")
                   or resp_data.get("id")
                   or "unknown")
        log.info(f"RELAYER_SUBMITTED {cid[:16]} task={task_id}")

        return _poll_relayer(task_id, relay_key)

    except Exception as e:
        log.warning(f"RELAYER_ERR {cid[:16]}: {e}")
        return False


def _poll_relayer(task_id: str, relay_key: str) -> bool:
    """
    [ASSUMPTION] Poll relayer task status.
    Conservative: max 6 polls, 30s apart (3 min total).
    Only returns True on explicit terminal success status.
    """
    import requests
    if task_id == "unknown":
        log.warning("POLL_SKIP: no task_id from relayer response")
        return False

    headers = {"Authorization": f"Bearer {relay_key}", "x-api-key": relay_key}
    # ASSUMPTION: relayer status endpoint
    status_url = f"{RELAYER_URL}/status/{task_id}"

    for poll in range(1, 7):
        time.sleep(30)
        try:
            r = requests.get(status_url, headers=headers, timeout=10)
            if r.status_code != 200:
                log.warning(f"POLL_HTTP_{r.status_code} poll={poll}/6")
                continue
            data = r.json()
            status = str(data.get("status", "")).lower()
            log.info(f"POLL {poll}/6 task={task_id} status={status}")
            if status in ("success", "confirmed", "mined", "complete", "executed"):
                log.info(f"RELAYER_CONFIRMED task={task_id}")
                return True
            if status in ("failed", "cancelled", "reverted", "error"):
                log.warning(f"RELAYER_TERMINAL_FAIL task={task_id} status={status}")
                return False
            # pending/submitted/processing — keep polling
        except Exception as e:
            log.warning(f"POLL_ERR poll={poll}/6: {e}")

    log.warning(f"POLL_TIMEOUT task={task_id} after 6 polls")
    return False


# ── Main cycle ────────────────────────────────────────────────────────────────
def _run_cycle(state: dict) -> None:
    """
    One full attempt cycle.
    Mutates state in-place. Calls _save_state() after each redeem.
    """
    now = time.time()

    # ── Global throttle ───────────────────────────────────────────────────────
    since_last = now - state.get("last_global_attempt", 0.0)
    if since_last < GLOBAL_INTERVAL:
        log.debug(f"THROTTLE: next cycle in {int(GLOBAL_INTERVAL - since_last)}s")
        return

    # ── Credentials ───────────────────────────────────────────────────────────
    pk = os.getenv("PM_PRIVATE_KEY", "").strip()
    funder = os.getenv("PM_FUNDER_KEY", "").strip() or _derive_eoa(pk)
    if not pk:
        log.warning("SKIP: PM_PRIVATE_KEY not set")
        return
    if not funder:
        log.warning("SKIP: PM_FUNDER_KEY not set and EOA derivation failed")
        return

    log.info(f"CYCLE_START funder={funder[:10]}...")
    state["last_global_attempt"] = now

    # ── Fetch redeemable positions ────────────────────────────────────────────
    positions = _fetch_redeemable(funder)
    if not positions:
        log.info("CYCLE_END: no redeemable positions")
        _save_state(state)
        return

    redeemed_set: Set[str] = set(state.get("redeemed_cids", []))
    cid_attempts: Dict[str, float] = state.get("cid_last_attempt", {})
    stats = state.setdefault("stats", {"total_redeemed": 0, "total_failed": 0, "total_skipped": 0})

    # ── Filter candidates ─────────────────────────────────────────────────────
    candidates = []
    for p in positions:
        cid = _extract_cid(p)
        if not cid:
            continue

        if cid in redeemed_set:
            log.debug(f"SKIP already_redeemed: {cid[:16]}")
            stats["total_skipped"] += 1
            continue

        last_try = cid_attempts.get(cid, 0.0)
        cooldown_remaining = PER_CID_COOLDOWN - (now - last_try)
        if cooldown_remaining > 0:
            log.info(f"SKIP cooldown: {cid[:16]} retry_in={int(cooldown_remaining)}s")
            stats["total_skipped"] += 1
            continue

        value = float(p.get("currentValue", 0.0))
        if value <= 0.0:
            log.info(f"SKIP no_value: {cid[:16]} currentValue={value}")
            stats["total_skipped"] += 1
            continue

        candidates.append((cid, value))

    if not candidates:
        log.info("CYCLE_END: all candidates filtered")
        _save_state(state)
        return

    log.info(f"CANDIDATES: {len(candidates)} to attempt")

    # ── Attempt each candidate ────────────────────────────────────────────────
    for cid, value in candidates:
        log.info(f"ATTEMPT: {cid[:16]} currentValue=${value:.4f}")

        # On-chain guard: skip if UMA not yet resolved
        denom = _payout_denominator(cid)
        if denom == 0:
            log.warning(f"SKIP onchain_unresolved: {cid[:16]} payoutDenominator=0 (UMA pending)")
            stats["total_skipped"] += 1
            # Don't set cid_last_attempt here — let it retry next cycle
            continue

        # Primary path: py_clob_client (gasless, proven)
        success = _redeem_via_clob(cid, pk, funder)

        # Secondary path: direct relayer (ASSUMPTION — only if primary failed)
        if not success and os.getenv("RELAYER_API_KEY"):
            log.info(f"FALLBACK_RELAYER: {cid[:16]}")
            success = _redeem_via_relayer(cid, pk, funder)

        # Record outcome
        cid_attempts[cid] = now
        state["cid_last_attempt"] = cid_attempts

        if success:
            redeemed_set.add(cid)
            state["redeemed_cids"] = list(redeemed_set)
            stats["total_redeemed"] += 1
            log.info(f"SUCCESS: {cid[:16]} total_redeemed={stats['total_redeemed']}")
        else:
            stats["total_failed"] += 1
            log.warning(f"FAILED: {cid[:16]} cooldown={PER_CID_COOLDOWN//3600}h "
                        f"total_failed={stats['total_failed']}")

        _save_state(state)
        time.sleep(2)  # small gap between individual redeems

    log.info(f"CYCLE_END: redeemed={stats['total_redeemed']} "
             f"failed={stats['total_failed']} skipped={stats['total_skipped']}")


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    if os.getenv(ENABLE_ENV, "").lower() not in ("true", "1", "yes"):
        log.info(f"DISABLED: set {ENABLE_ENV}=true to enable. Exiting.")
        sys.exit(0)

    log.info("=" * 60)
    log.info("REDEEMER WORKER STARTED")
    log.info(f"  state_file:      {STATE_FILE}")
    log.info(f"  log_file:        {LOG_FILE}")
    log.info(f"  global_interval: {GLOBAL_INTERVAL}s (1/hour max)")
    log.info(f"  per_cid_cooldown:{PER_CID_COOLDOWN}s (24h)")
    log.info(f"  main_sleep:      {MAIN_SLEEP}s")
    log.info("=" * 60)

    state = _load_state()
    log.info(f"STATE_LOADED: {len(state.get('redeemed_cids', []))} already redeemed, "
             f"last_attempt={state.get('last_global_attempt', 0):.0f}")

    while True:
        try:
            _run_cycle(state)
        except KeyboardInterrupt:
            log.info("SHUTDOWN: KeyboardInterrupt")
            _save_state(state)
            sys.exit(0)
        except Exception as e:
            log.error(f"CYCLE_UNHANDLED_ERR: {e}")
        log.debug(f"SLEEP {MAIN_SLEEP}s...")
        time.sleep(MAIN_SLEEP)


if __name__ == "__main__":
    main()
