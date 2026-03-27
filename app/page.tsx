"use client";
import React, { useState, useEffect, useCallback } from 'react';
import {
  ComposedChart, BarChart, Bar, Area, Cell,
  XAxis, YAxis, ReferenceLine, Tooltip, ResponsiveContainer,
} from 'recharts';

// ============================================================================
// Types
// ============================================================================
interface Position {
  id: string;
  title: string;
  size: string | number;
  currentValue: number;
  cashPnl: number;
  percentPnl: number;
  entryPrice: number;
  currentPrice: number;
  outcome: string;
  /** Quantitative edge at entry (from trades.jsonl via S3). Present only when S3 active. */
  edgeEntry?: number;
}

interface BucketData {
  bucket: string;
  modelProb: number;
  marketProb: number;
  hasLiveData: boolean;
}

interface TradeRecord {
  market: string;
  pnl: number;
  isWin: boolean;
}

type ActiveTab = 'terminal' | 'pnl' | 'positions' | 'settings';

// ============================================================================
// Constants
// ============================================================================
const NEUTRAL_FALLBACK = 0.15;
const EV_THRESHOLD    = 0.05;
const DUST_THRESHOLD  = 0.10;
const LS_WALLET_KEY   = 'lariant_wallet';
const DEFAULT_WALLET  = '0x3a6Fe25c5320dC81F35F0f4e6bE8bDc76BC5e1c7';
// "Clean Slate" hard cutoff — only activity STRICTLY AFTER this UTC moment counts.
// Update the string to move the performance clock forward at any time.
const PNL_CUTOFF_TIMESTAMP = new Date('2026-03-20T10:15:00Z').getTime();

// ============================================================================
// Math Helpers — Normal CDF (Abramowitz & Stegun, error < 1.5e-7)
// ============================================================================
function erf(x: number): number {
  const sign = x >= 0 ? 1 : -1;
  const ax   = Math.abs(x);
  const t    = 1 / (1 + 0.3275911 * ax);
  const y    = t * (0.254829592 +
               t * (-0.284496736 +
               t * ( 1.421413741 +
               t * (-1.453152027 +
               t *   1.061405429))));
  return sign * (1 - y * Math.exp(-ax * ax));
}

function normalCDF(x: number, mu: number, sigma: number): number {
  return 0.5 * (1 + erf((x - mu) / (sigma * Math.SQRT2)));
}

// ============================================================================
// Phase 2a Calibration Matrix — per-city, per-calendar-month.
// Mirrors tempbot.py _CALIB_MATRIX exactly.
// bias  : systematic model over/underestimate (°C). adj_fcst = forecast - bias.
// sigma : predictive spread (°C) fed to normalCDF.
// tdays : forecast horizon in days (currently always 1 for T+1 product).
// ============================================================================
const _CALIB_MATRIX: Record<'LON' | 'SEO', Record<number, { bias: number; sigma: number }>> = {
  LON: {
    1:  { bias: -0.8, sigma: 2.4 },
    2:  { bias: -0.5, sigma: 2.3 },
    3:  { bias:  0.1, sigma: 2.2 },
    4:  { bias:  0.3, sigma: 2.0 },
    5:  { bias:  0.4, sigma: 1.9 },
    6:  { bias:  0.5, sigma: 1.8 },
    7:  { bias:  0.4, sigma: 1.7 },
    8:  { bias:  0.3, sigma: 1.8 },
    9:  { bias:  0.1, sigma: 1.9 },
    10: { bias: -0.2, sigma: 2.1 },
    11: { bias: -0.5, sigma: 2.3 },
    12: { bias: -0.7, sigma: 2.4 },
  },
  SEO: {
    1:  { bias: -1.2, sigma: 3.2 },
    2:  { bias: -0.8, sigma: 3.0 },
    3:  { bias: -0.2, sigma: 2.8 },
    4:  { bias:  0.3, sigma: 2.5 },
    5:  { bias:  0.6, sigma: 2.3 },
    6:  { bias:  0.8, sigma: 2.1 },
    7:  { bias:  0.5, sigma: 2.4 },
    8:  { bias:  0.3, sigma: 2.3 },
    9:  { bias: -0.1, sigma: 2.5 },
    10: { bias: -0.5, sigma: 2.7 },
    11: { bias: -0.8, sigma: 3.0 },
    12: { bias: -1.1, sigma: 3.2 },
  },
} as const;

/**
 * Phase 2a: look up bias + sigma for a given city, month, and horizon.
 * Mirrors tempbot.py get_dynamic_calibration().
 * @param city   'LON' | 'SEO'
 * @param month  1–12 (calendar month)
 * @param tdays  forecast horizon in days (default 1)
 */
function get_dynamic_calibration(
  city: 'LON' | 'SEO',
  month: number,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _tdays = 1,
): { bias: number; sigma: number } {
  return _CALIB_MATRIX[city]?.[month] ?? { bias: 0, sigma: city === 'LON' ? 2.2 : 2.8 };
}

function matchBucketToPositions(label: string, positions: Position[]): number | null {
  const underMatch = label.match(/^<(\d+)/);
  const overMatch  = label.match(/^>(\d+)/);
  const rangeMatch = label.match(/^(\d+)-(\d+)/);

  for (const pos of positions) {
    const rawTitle = pos.title ?? (pos as any).question ?? (pos as any).market ?? '';
    const t = rawTitle.toLowerCase();
    const isWeather = t.includes('temp') || t.includes('°') || t.includes('celsius') ||
                      t.includes('weather') || t.includes('london') || t.includes('seoul');
    if (!isWeather) continue;

    if (underMatch) {
      const thresh = underMatch[1];
      if ((t.includes('below') || t.includes('under') || t.includes('less') || t.includes('<')) &&
          t.includes(thresh)) return pos.currentPrice;
    } else if (overMatch) {
      const thresh = overMatch[1];
      if ((t.includes('above') || t.includes('over') || t.includes('more') || t.includes('>')) &&
          t.includes(thresh)) return pos.currentPrice;
    } else if (rangeMatch) {
      if (t.includes(rangeMatch[1]) && t.includes(rangeMatch[2])) return pos.currentPrice;
    }
  }
  return null;
}

// adjForecast = raw forecast − bias (Phase 2a de-biasing applied by caller)
function buildBuckets(adjForecast: number, sigma: number, positions: Position[]): BucketData[] {
  const base = Math.floor(adjForecast) - 1;
  const p0   = normalCDF(base,     adjForecast, sigma);
  const p1   = normalCDF(base + 1, adjForecast, sigma);
  const p2   = normalCDF(base + 2, adjForecast, sigma);
  return [
    { label: `<${base}°C`,               modelProb: p0      },
    { label: `${base}-${base + 1}°C`,    modelProb: p1 - p0 },
    { label: `${base+1}-${base + 2}°C`,  modelProb: p2 - p1 },
    { label: `>${base + 2}°C`,           modelProb: 1 - p2  },
  ].map(({ label, modelProb }) => {
    const livePrice = matchBucketToPositions(label, positions);
    return { bucket: label, modelProb, marketProb: livePrice ?? NEUTRAL_FALLBACK, hasLiveData: livePrice !== null };
  });
}

// ============================================================================
// Recharts sub-components — module-level to avoid re-mount
// ============================================================================
const EvTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: '#191919', border: '1px solid #484848', padding: '6px 10px',
                  fontFamily: 'monospace', fontSize: '10px', lineHeight: 1.6, pointerEvents: 'none' }}>
      <div style={{ color: '#9fff88', marginBottom: 2 }}>{label}</div>
      {payload.map((e: any) => (
        <div key={e.dataKey} style={{ color: '#9fff88', opacity: e.dataKey === 'marketProb' ? 0.7 : 1 }}>
          {e.dataKey === 'modelProb' ? 'MODEL ' : 'MARKET'}: {(e.value * 100).toFixed(1)}%
        </div>
      ))}
    </div>
  );
};

const EvDot = (props: any) => {
  const { cx, cy, payload } = props;
  if (typeof cx !== 'number' || typeof cy !== 'number') return <g />;
  if (payload.modelProb > payload.marketProb + EV_THRESHOLD) {
    return (
      <circle cx={cx} cy={cy} r={5} fill="#9fff88"
        style={{ filter: 'drop-shadow(0 0 5px #9fff88)' }}
        className="ev-pulse-dot" />
    );
  }
  return <g />;
};

// ============================================================================
// InferenceChart — MUST live outside LariantTerminal.
// If defined inside the parent, React sees a new component type on every
// render (clock ticks every 1 s), causing full unmount → Recharts mount
// animation fires every second ("moving crazily").
// ============================================================================
interface InferenceChartProps {
  city: string;
  code: string;
  forecast: number | null;
  map: BucketData[];
  sigma: number;
}

function InferenceChart({ city, code, forecast, map, sigma }: InferenceChartProps) {
  // ── iOS touch fix ──────────────────────────────────────────────────────────
  // On iOS, Recharts tooltips become "sticky" after a tap because the browser
  // fires mousemove once on touchstart then never fires mouseleave.
  // Fix: track tooltip visibility in local state; dismiss on touchEnd + mouseleave.
  const [tooltipActive, setTooltipActive] = useState(true);

  return (
    <section className="border border-[#262626] bg-[#020202] relative overflow-hidden" style={{ height: '13rem' }}>
      <div className="absolute inset-0 flex flex-col p-3">
        {/* Header */}
        <div className="flex justify-between items-center shrink-0 mb-1">
          <span className="text-[8px] uppercase tracking-widest" style={{ color: '#9fff88', opacity: 0.7 }}>
            {city}
          </span>
          <span className="text-[8px] uppercase tracking-widest font-mono flex items-center gap-2" style={{ color: '#9fff88' }}>
            {forecast !== null ? `FV: ${forecast.toFixed(1)}°C` : `FETCHING ${code}…`}
            <span className="opacity-60 border border-[#9fff88] border-opacity-30 px-1 py-0.5 text-[7px]">
              σ={sigma.toFixed(1)}°C
            </span>
          </span>
        </div>
        {/* Legend */}
        <div className="flex gap-3 shrink-0 mb-1">
          {[
            { sw: <span className="inline-block w-3 h-2 bg-[#1a1a1a] border border-[#9fff88]" />, lbl: 'Market' },
            { sw: <span className="inline-block w-4 border-t border-dashed border-[#9fff88]" />,  lbl: 'Model'  },
            { sw: <span className="inline-block w-2 h-2 rounded-full bg-[#9fff88]" />,            lbl: '+EV'    },
          ].map(({ sw, lbl }) => (
            <span key={lbl} className="text-[7px] uppercase opacity-50 flex items-center gap-1">{sw}{lbl}</span>
          ))}
        </div>
        {/*
          Chart wrapper — iOS-specific touch fixes applied here:
          • touch-action: pan-y  → lets user scroll vertically through the chart
            without the SVG element capturing the gesture.
          • WebkitTapHighlightColor: transparent → removes the blue flash on tap.
          • user-select: none → prevents iOS text-selection loupe on long-press.
          • onTouchEnd: dismisses the tooltip 200 ms after the finger lifts.
            The delay keeps the tooltip visible just long enough for the user to
            read it before it fades, matching iOS "tap-to-see, lift-to-dismiss".
          • onMouseLeave: dismisses on desktop hover-out; re-enables on re-enter.
        */}
        <div
          style={{
            height: 140, overflow: 'hidden',
            touchAction: 'pan-y',
            WebkitTapHighlightColor: 'transparent',
            userSelect: 'none',
          }}
          onTouchStart={() => setTooltipActive(true)}
          onTouchEnd={() => setTimeout(() => setTooltipActive(false), 200)}
          onMouseEnter={() => setTooltipActive(true)}
          onMouseLeave={() => setTooltipActive(false)}
        >
          {map.length === 0 ? (
            <div style={{ height: 140, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#9fff88', fontSize: '8px', textTransform: 'uppercase', letterSpacing: '0.1em' }} className="animate-pulse">
              Computing…
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={140}>
              <ComposedChart data={map} margin={{ top: 10, right: 0, bottom: 0, left: -20 }}>
                <defs>
                  <linearGradient id={`grad_${code}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%"   stopColor="#9fff88" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#9fff88" stopOpacity={0}    />
                  </linearGradient>
                  <filter id={`glow_${code}`} x="-20%" y="-20%" width="140%" height="140%">
                    <feGaussianBlur stdDeviation="2" result="blur" />
                    <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
                  </filter>
                </defs>
                {/* Tooltip only rendered when active — prevents sticky iOS hover state */}
                {tooltipActive && (
                  <Tooltip
                    content={<EvTooltip />}
                    cursor={{ stroke: '#2a2a2a', strokeWidth: 1 }}
                    wrapperStyle={{ pointerEvents: 'none' }}
                  />
                )}
                <XAxis dataKey="bucket" axisLine={false} tickLine={false}
                  interval="preserveStartEnd" tickMargin={5}
                  tick={{ fill: '#ababab', fontSize: 10, fontFamily: 'monospace' }} />
                <YAxis domain={[0, 1]} hide />
                {map.filter(d => d.modelProb > d.marketProb + EV_THRESHOLD).map(d => (
                  <ReferenceLine key={d.bucket} x={d.bucket} stroke="#9fff88"
                    strokeDasharray="4 4" strokeWidth={1} strokeOpacity={0.5} />
                ))}
                <Bar dataKey="marketProb" radius={[2,2,0,0]} maxBarSize={36} strokeWidth={1.5}
                  isAnimationActive={false}>
                  {map.map(e => (
                    <Cell key={e.bucket}
                      fill={e.hasLiveData ? '#1a1a1a' : '#0d0d0d'}
                      stroke={e.hasLiveData ? '#9fff88' : '#2a2a2a'}
                      fillOpacity={e.hasLiveData ? 1 : 0.5} />
                  ))}
                </Bar>
                <Area type="monotone" dataKey="modelProb"
                  fill={`url(#grad_${code})`} stroke="#9fff88"
                  strokeWidth={1.5} strokeDasharray="4 4"
                  filter={`url(#glow_${code})`}
                  dot={(p: any) => <EvDot {...p} />}
                  activeDot={false}
                  isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </section>
  );
}

// ============================================================================
// Utility helpers — outside component to avoid re-creation on every render
// ============================================================================
const fmt = (val: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);

const pct = (val: number) => `${val >= 0 ? '+' : ''}${val.toFixed(2)}%`;

// ============================================================================
// fetchUsdcBalance — queries BOTH Polymarket USDC contracts on Polygon and
// returns their SUM, so the balance is correct regardless of which contract
// holds the funds (Polymarket migrated from USDC.e to native USDC in 2024).
//
// Contracts checked in parallel:
//   USDC.e  (bridged)  0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
//   USDC    (native)   0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359
//
// RPC cascade (tried in order until one succeeds):
//   1. Alchemy  https://polygon-mainnet.g.alchemy.com/v2/yynv8Dor1C-RYgiInJk2V
//   2. Ankr     https://rpc.ankr.com/polygon
//   3. Public   https://polygon-rpc.com
//
// Selector : balanceOf(address) = 0x70a08231
// ABI enc. : strip 0x, lowercase, left-pad address to 64 hex chars (32 bytes)
// Decimals : 6 on both contracts
// ============================================================================
const USDC_CONTRACTS = [
  { name: 'USDC.e', addr: '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174' },
  { name: 'USDC',   addr: '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359' },
] as const;

const RPC_CASCADE = [
  'https://polygon-mainnet.g.alchemy.com/v2/yynv8Dor1C-RYgiInJk2V',
  'https://rpc.ankr.com/polygon',
  'https://polygon-rpc.com',
] as const;

async function rpcCall(rpcUrl: string, to: string, data: string): Promise<number> {
  const res = await fetch(rpcUrl, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0', id: 1, method: 'eth_call',
      params: [{ to, data }, 'latest'],
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const d = await res.json();
  if (!d?.result || d.result === '0x' || d.result === '0x0') return 0;
  const raw = parseInt(d.result, 16);
  return isFinite(raw) ? raw / 1_000_000 : 0;
}

async function fetchUsdcBalance(walletAddress: string): Promise<number> {
  if (!walletAddress || typeof walletAddress !== 'string') return 0;

  // ABI-encode: strip 0x, lowercase, pad to 32 bytes (64 hex chars)
  const paddedAddress = walletAddress.toLowerCase().replace(/^0x/, '').padStart(64, '0');
  const dataPayload   = '0x70a08231' + paddedAddress;

  // Query both contracts in parallel via the first RPC that responds
  let totalBalance = 0;
  for (const rpcUrl of RPC_CASCADE) {
    try {
      // Both contracts share the same call structure — run them in parallel
      const results = await Promise.all(
        USDC_CONTRACTS.map(c => rpcCall(rpcUrl, c.addr, dataPayload))
      );
      totalBalance = results.reduce((s, v) => s + v, 0);
      console.log(
        `USDC [${rpcUrl.split('/')[2]}]:`,
        USDC_CONTRACTS.map((c, i) => `${c.name}=${results[i].toFixed(4)}`).join(' '),
        `→ total $${totalBalance.toFixed(4)}`
      );
      return totalBalance; // success — no need to try next RPC
    } catch (e) {
      console.warn(`USDC RPC failed (${rpcUrl.split('/')[2]}):`, (e as Error).message);
      // fall through to next RPC in cascade
    }
  }

  console.error('USDC: all RPCs failed — returning 0');
  return 0;
}

// ============================================================================
// PNL Math Engine — shadow FIFO accounting from raw Polymarket activity feed.
//
// Field-resolution strategy (Polymarket API varies by endpoint version):
//   assetId  : asset_id | token_id | condition_id | conditionId
//   side     : type ("BUY"/"SELL"/"TRADE"+"side"/"REDEEM"/"CLAIM"/"SETTLEMENT")
//   dollars  : usdcAmount | amount | (shares × price) — first non-NaN wins
//   shares   : shares | size | outcome_shares
//   price    : price  (used only when dollars field is absent)
//
// Precision: all dollar amounts are stored as integer micro-dollars (×1e6)
// to eliminate IEEE-754 drift, then converted back at output time.
// Each record is wrapped in try/catch — one bad activity row never aborts run.
// ============================================================================
function computeTradesFromActivity(activities: any[]): TradeRecord[] {
  if (!Array.isArray(activities) || activities.length === 0) return [];

  interface Bucket {
    netCostUc:    number;   // net cost in micro-dollars (integer)
    sharesHeldUc: number;   // net shares × 1e6 (integer)
    market:       string;
  }
  const groups = new Map<string, Bucket>();

  // Helper: parse any numeric field, return NaN if unparseable
  const num = (v: any): number => {
    if (v === null || v === undefined || v === '') return NaN;
    const n = typeof v === 'number' ? v : parseFloat(String(v).replace(/,/g, ''));
    return isFinite(n) ? n : NaN;
  };
  // Safe micro-dollar encode: round to nearest integer μUSD
  const toUc  = (d: number): number => Math.round(d * 1_000_000);
  const fromUc = (uc: number): number => uc / 1_000_000;

  // Sort chronologically — oldest first
  const sorted = [...activities].sort((a, b) => {
    try {
      const ta = a.timestamp ?? a.createdAt ?? a.created_at ?? 0;
      const tb = b.timestamp ?? b.createdAt ?? b.created_at ?? 0;
      const na  = typeof ta === 'string' ? new Date(ta).getTime() : Number(ta) * (String(ta).length <= 10 ? 1000 : 1);
      const nb  = typeof tb === 'string' ? new Date(tb).getTime() : Number(tb) * (String(tb).length <= 10 ? 1000 : 1);
      return na - nb;
    } catch { return 0; }
  });

  const trades: TradeRecord[] = [];

  for (const act of sorted) {
    try {
      // ── Asset key ─────────────────────────────────────────────────────────
      const assetId: string = String(act.asset_id ?? act.token_id ?? act.condition_id ?? act.conditionId ?? '').trim();
      if (!assetId) continue;

      // ── Side resolution ───────────────────────────────────────────────────
      // Polymarket uses: type="BUY"|"SELL"|"REDEEM"|"CLAIM"|"SETTLEMENT"
      // Occasionally type="TRADE" with a nested side="BUY"|"SELL" field
      const rawType  = String(act.type  ?? '').toUpperCase().trim();
      const rawSide  = String(act.side  ?? act.direction ?? '').toUpperCase().trim();
      const sideKey  = rawType === 'TRADE' ? rawSide : rawType;

      const isBuy    = sideKey === 'BUY'  || sideKey === 'PURCHASE';
      const isSell   = sideKey === 'SELL' || sideKey === 'REDEEM' ||
                       sideKey === 'CLAIM' || sideKey === 'SETTLEMENT' || sideKey === 'WIN';
      if (!isBuy && !isSell) continue; // skip TRANSFER, MERGE, etc.

      // ── Dollar amount ─────────────────────────────────────────────────────
      const sharesRaw = num(act.shares ?? act.size ?? act.outcome_shares);
      const priceRaw  = num(act.price  ?? act.avg_price);

      // Resolution order:
      //  1. Explicit dollar field (usdcAmount / usdc_amount / amount / cashflow)
      let dollarsRaw = num(act.usdcAmount ?? act.usdc_amount ?? act.amount ?? act.cashflow);

      //  2. shares × price when dollar field absent
      if (isNaN(dollarsRaw) && !isNaN(sharesRaw) && !isNaN(priceRaw)) {
        dollarsRaw = sharesRaw * priceRaw;
      }

      //  3. $1.00/share WIN payout — ONLY for explicit win-type events.
      //     Do NOT apply to generic REDEEM: it could be the losing side (gets $0).
      //     Do NOT apply to SELL: it has its own market price.
      if (isNaN(dollarsRaw) && (sideKey === 'WIN' || sideKey === 'CLAIM') && !isNaN(sharesRaw)) {
        dollarsRaw = sharesRaw; // $1.00 per winning share
      }

      // shares must be positive to do any accounting
      const shares = isNaN(sharesRaw) || sharesRaw <= 0 ? null : sharesRaw;

      // dollars rules differ by side:
      //   BUY  → must be > 0 (you can't buy for free; $0 means bad data → skip)
      //   SELL → allow $0 (position expired worthless = full loss of cost basis)
      //          still skip if completely unknown (NaN)
      let dollars: number | null;
      if (isNaN(dollarsRaw)) {
        dollars = null;                                   // unknown → skip
      } else if (isBuy) {
        dollars = dollarsRaw > 0 ? dollarsRaw : null;    // BUY must cost something
      } else {
        dollars = Math.max(0, dollarsRaw);                // SELL: clamp to 0, allow loss
      }

      if (shares === null || dollars === null) continue;

      // ── Market label ──────────────────────────────────────────────────────
      const market = String(act.question ?? act.title ?? act.market ?? act.slug ?? 'Unknown Market').slice(0, 80);

      // ── Cost-basis accounting in μUSD ─────────────────────────────────────
      if (!groups.has(assetId)) groups.set(assetId, { netCostUc: 0, sharesHeldUc: 0, market });
      const g = groups.get(assetId)!;

      if (isBuy) {
        g.netCostUc    += toUc(dollars);
        g.sharesHeldUc += toUc(shares);
      } else {
        // isSell / REDEEM / CLAIM
        if (g.sharesHeldUc <= 0) continue; // no open position to close

        // Pro-rata cost basis for the exited shares
        const exitSharesUc   = Math.min(toUc(shares), g.sharesHeldUc); // clamp to held
        const avgCostUcPerSh = g.netCostUc / g.sharesHeldUc;           // μUSD per μshare
        const costBasisUc    = Math.round(avgCostUcPerSh * exitSharesUc);
        const proceedsUc     = toUc(dollars);
        const realizedUc     = proceedsUc - costBasisUc;

        // Record the realized trade
        const realizedDollars = fromUc(realizedUc);
        trades.push({
          market:  g.market || market,
          pnl:     Math.round(realizedDollars * 10_000) / 10_000, // 4dp
          isWin:   realizedUc > 0,
        });

        // Reduce open position
        g.sharesHeldUc = Math.max(0, g.sharesHeldUc - exitSharesUc);
        g.netCostUc    = g.sharesHeldUc > 0
          ? Math.round(avgCostUcPerSh * g.sharesHeldUc)
          : 0;
      }
    } catch {
      // Silently skip malformed activity records — never let one bad row abort the engine
    }
  }

  return trades;
}

// ============================================================================
// PnlBarTooltip — module-level (no re-mount on clock tick)
// ============================================================================
const PnlBarTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  const val: number = payload[0]?.value ?? 0;
  return (
    <div style={{ background: '#191919', border: '1px solid #484848', padding: '6px 10px', fontFamily: 'monospace', fontSize: '10px', lineHeight: 1.6, pointerEvents: 'none' }}>
      <div style={{ color: '#ababab', marginBottom: 2 }}>{label}</div>
      <div style={{ color: val >= 0 ? '#9fff88' : '#ff706f' }}>
        PnL: {val >= 0 ? '+' : ''}{val.toFixed(4)}
      </div>
    </div>
  );
};

// ============================================================================
// PnlChart — MUST live at module level (same reason as InferenceChart:
// clock tick re-renders would unmount/remount and thrash the Recharts DOM).
// ============================================================================
function PnlChart({ trades }: { trades: TradeRecord[] }) {
  const last20 = trades.slice(-20);
  const data   = last20.map((t, i) => ({ name: `T${trades.length - last20.length + i + 1}`, pnl: parseFloat(t.pnl.toFixed(4)), isWin: t.isWin }));
  // iOS touch fix — same pattern as InferenceChart
  const [tooltipActive, setTooltipActive] = useState(true);
  return (
    /*
     * touch-action: pan-y  → vertical scroll passes through unhindered on iPhone.
     * onTouchEnd + 200 ms delay → tap shows tooltip briefly, then auto-dismisses.
     */
    <div
      style={{
        height: 140, overflow: 'hidden',
        touchAction: 'pan-y',
        WebkitTapHighlightColor: 'transparent',
        userSelect: 'none',
      }}
      onTouchStart={() => setTooltipActive(true)}
      onTouchEnd={() => setTimeout(() => setTooltipActive(false), 200)}
      onMouseEnter={() => setTooltipActive(true)}
      onMouseLeave={() => setTooltipActive(false)}
    >
      {data.length === 0 ? (
        <div style={{ height: 140, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#9fff88', fontSize: '8px', textTransform: 'uppercase', letterSpacing: '0.1em', opacity: 0.4 }}>
          No closed trades yet
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={140}>
          <BarChart data={data} margin={{ top: 10, right: 0, bottom: 0, left: -20 }}>
            <XAxis dataKey="name" axisLine={false} tickLine={false} interval="preserveStartEnd" tickMargin={5} tick={{ fill: '#ababab', fontSize: 8, fontFamily: 'monospace' }} />
            <YAxis axisLine={false} tickLine={false} tick={{ fill: '#ababab', fontSize: 8, fontFamily: 'monospace' }} />
            <ReferenceLine y={0} stroke="#383838" strokeWidth={1} />
            {tooltipActive && (
              <Tooltip
                content={<PnlBarTooltip />}
                cursor={{ fill: '#1a1a1a' }}
                wrapperStyle={{ pointerEvents: 'none' }}
              />
            )}
            <Bar dataKey="pnl" isAnimationActive={false} radius={[2, 2, 0, 0]}>
              {data.map((entry, idx) => (
                <Cell key={`cell-${idx}`} fill={entry.isWin ? '#9fff88' : '#ff706f'} fillOpacity={0.85} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================
export default function LariantTerminal() {
  // ── State ─────────────────────────────────────────────────────────────────
  // isMounted MUST be first — used to gate the entire render and prevent
  // React Hydration Error #418 (server HTML ≠ client initial render).
  const [isMounted,    setIsMounted]    = useState(false);
  const [activeTab,    setActiveTab]    = useState<ActiveTab>('terminal');
  const [walletAddr,   setWalletAddr]   = useState(DEFAULT_WALLET);
  const [walletInput,  setWalletInput]  = useState(DEFAULT_WALLET);
  const [positions,    setPositions]    = useState<Position[]>([]);
  // usdcBalance — on-chain from Polygon RPC; null = not yet fetched
  // Kept separate from tradePnls so an RPC failure never affects the PNL tab UI
  const [usdcBalance,  setUsdcBalance]  = useState<number | null>(null);
  const [mapLon,       setMapLon]       = useState<BucketData[]>([]);
  const [mapSeo,       setMapSeo]       = useState<BucketData[]>([]);
  const [fcastLon,     setFcastLon]     = useState<number | null>(null);
  const [fcastSeo,     setFcastSeo]     = useState<number | null>(null);
  // Phase 2a: dynamic sigma from calibration matrix (shown in inference chart header)
  const [sigmaLon,     setSigmaLon]     = useState<number>(2.2);
  const [sigmaSeo,     setSigmaSeo]     = useState<number>(2.8);
  // Phase 2 ML dataset collection — sourced from S3 trades.jsonl
  // version="v2-bias-patch-stable" + event="ENTRY" records only
  const [phase2Count,  setPhase2Count]  = useState<number>(0);
  const [phase2Edge,   setPhase2Edge]   = useState<number>(0);
  const [tradePnls,    setTradePnls]    = useState<TradeRecord[]>([]);
  const [loadingPnl,   setLoadingPnl]   = useState(false);
  const [pnlFetched,   setPnlFetched]   = useState(false);
  const [loadingPos,   setLoadingPos]   = useState(false);
  const [clock,        setClock]        = useState('');
  // Empty on SSR — populated after mount to avoid toLocaleTimeString() mismatch
  const [logs,         setLogs]         = useState<string[]>([]);

  // ── Helpers ───────────────────────────────────────────────────────────────
  const log = useCallback((msg: string) => {
    setLogs(prev => [...prev.slice(-60), `[${new Date().toLocaleTimeString()}] ${msg}`]);
  }, []);

  // ── Mount + Bootstrap ─────────────────────────────────────────────────────
  // Single effect — fires once, client-side only.
  // 1. setIsMounted(true) → unblocks the full render (hydration guard)
  // 2. Seeds the event log with timestamped boot messages (safe: client only)
  // 3. Loads wallet from localStorage, falling back to DEFAULT_WALLET
  useEffect(() => {
    setIsMounted(true);
    const now = new Date().toLocaleTimeString();
    setLogs([
      `[${now}] SYS: Booting LARIANT v1.0...`,
      `[${now}] NET: Shadow Node online — wallet ${DEFAULT_WALLET.slice(0,6)}…${DEFAULT_WALLET.slice(-4)}`,
    ]);
    if (typeof window !== 'undefined') {
      const w = localStorage.getItem(LS_WALLET_KEY) || DEFAULT_WALLET;
      setWalletAddr(w); setWalletInput(w);
    }
  }, []);

  // ── Clock ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    const iv = setInterval(() => setClock(new Date().toISOString().slice(11, 19) + ' UTC'), 1000);
    return () => clearInterval(iv);
  }, []);

  // ── S3 live data — fetches positions.json + trades.jsonl every 30 s ───────
  // /api/s3data is a Next.js API route that reads from AWS S3 using the
  // @aws-sdk/client-s3 package (or proxies to s3_api.py if S3_API_URL is set).
  //
  // When S3 is available (s3Available: true):
  //   • Positions are sourced from S3 positions.json (includes edgeEntry).
  //   • sigmaLon / sigmaSeo are overridden with the latest sigma_used from trades.jsonl.
  //   • The Polymarket /positions polling is kept running as a live-price fallback.
  //
  // When S3 is not configured (S3_BUCKET env var absent):
  //   • s3Available: false  → no-op here; existing Polymarket/calibration paths continue.
  useEffect(() => {
    let cancelled = false;

    const fetchS3 = async () => {
      try {
        const res = await fetch('/api/s3data', { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: {
          s3Available:      boolean;
          positions:        Position[];
          sigmaLon:         number;
          sigmaSeo:         number;
          phase2EntryCount: number;
          phase2AvgEdge:    number;
        } = await res.json();

        if (cancelled) return;

        if (!data.s3Available) {
          // S3 not configured — silently skip, existing state unchanged
          return;
        }

        // ── Override sigma from S3 trades.jsonl (most recent sigma_used per city) ──
        // Triggers the EV-map useEffects below to rebuild with the new sigma.
        if (isFinite(data.sigmaLon)) setSigmaLon(data.sigmaLon);
        if (isFinite(data.sigmaSeo)) setSigmaSeo(data.sigmaSeo);

        // ── Override positions from S3 positions.json ──────────────────────
        // S3 includes edgeEntry; Polymarket API does not.
        // If S3 returns an empty array, fall through to the Polymarket data already
        // in state — don't wipe out live data if the bot hasn't written yet.
        // ── Phase 2 metrics — always update when S3 is available ─────────────
        // Updated even when positions[] is empty so the widget counts correctly
        // while the bot has not opened any new positions yet.
        if (isFinite(data.phase2EntryCount)) setPhase2Count(data.phase2EntryCount);
        if (isFinite(data.phase2AvgEdge))    setPhase2Edge(data.phase2AvgEdge);

        // ── Override positions from S3 positions.json ──────────────────────
        // S3 includes edgeEntry; Polymarket API does not.
        // If S3 returns an empty array, fall through to Polymarket state.
        if (Array.isArray(data.positions) && data.positions.length > 0) {
          setPositions(data.positions);
          log(`S3: ${data.positions.length} pos | σLON=${data.sigmaLon} σSEO=${data.sigmaSeo} | P2 ${data.phase2EntryCount}/300 edge=${(data.phase2AvgEdge * 100).toFixed(2)}%`);
        }
      } catch (err: unknown) {
        // Non-fatal: S3 API not reachable — existing state (Polymarket) persists
        const msg = err instanceof Error ? err.message : String(err);
        log(`WARN: S3 fetch failed (${msg}) — using Polymarket fallback`);
      }
    };

    fetchS3(); // immediate first fetch
    const iv = setInterval(fetchS3, 30_000); // then every 30 s
    return () => { cancelled = true; clearInterval(iv); };
  }, [log]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Weather: London 51.5048, 0.0495 ───────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch('https://api.open-meteo.com/v1/forecast?latitude=51.5048&longitude=0.0495&daily=temperature_2m_max&timezone=Europe%2FLondon&forecast_days=2');
        const d = await r.json();
        const t: number = d.daily.temperature_2m_max[1];
        setFcastLon(t);
        log(`METEO: LON T+1 MAX = ${t.toFixed(1)}°C`);
      } catch {
        setFcastLon(14.0);
        log('WARN: Open-Meteo LON failed — fallback 14.0°C');
      }
    })();
  }, [log]);

  // ── Weather: Seoul 37.4691, 126.4510 ──────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch('https://api.open-meteo.com/v1/forecast?latitude=37.4691&longitude=126.4510&daily=temperature_2m_max&timezone=Asia%2FSeoul&forecast_days=2');
        const d = await r.json();
        const t: number = d.daily.temperature_2m_max[1];
        setFcastSeo(t);
        log(`METEO: SEO T+1 MAX = ${t.toFixed(1)}°C`);
      } catch {
        setFcastSeo(18.0);
        log('WARN: Open-Meteo SEO failed — fallback 18.0°C');
      }
    })();
  }, [log]);

  // ── EV Map: rebuild when forecast OR positions change — Phase 2a pricing ──
  // Uses get_dynamic_calibration() for month-aware bias + sigma.
  // adj_fcst = raw_forecast − bias (mirrors tempbot.py Phase 2a formula).
  useEffect(() => {
    if (fcastLon === null) return;
    const month = new Date().getMonth() + 1;
    const { bias, sigma } = get_dynamic_calibration('LON', month);
    const adj_fcst = fcastLon - bias;
    setSigmaLon(sigma);
    const b = buildBuckets(adj_fcst, sigma, positions);
    setMapLon(b);
    log(`INF: LON map rebuilt — adj_fcst=${adj_fcst.toFixed(1)}°C bias=${bias} σ=${sigma} — ${b.filter(x => x.hasLiveData).length}/4 live prices`);
  }, [fcastLon, positions, log]);

  useEffect(() => {
    if (fcastSeo === null) return;
    const month = new Date().getMonth() + 1;
    const { bias, sigma } = get_dynamic_calibration('SEO', month);
    const adj_fcst = fcastSeo - bias;
    setSigmaSeo(sigma);
    const b = buildBuckets(adj_fcst, sigma, positions);
    setMapSeo(b);
    log(`INF: SEO map rebuilt — adj_fcst=${adj_fcst.toFixed(1)}°C bias=${bias} σ=${sigma} — ${b.filter(x => x.hasLiveData).length}/4 live prices`);
  }, [fcastSeo, positions, log]);

  // ── USDC balance — Polygon RPC, auth-free, every 60 s ────────────────────
  // Stored in its own state so an RPC failure never touches PNL or positions.
  useEffect(() => {
    if (!walletAddr || walletAddr.length < 10) return;
    let cancelled = false;
    const run = async () => {
      const usdc = await fetchUsdcBalance(walletAddr);
      if (!cancelled) {
        setUsdcBalance(usdc);
        log(`RPC: USDC balance (USDC.e + USDC native) = ${fmt(usdc)}`);
      }
    };
    run();
    const iv = setInterval(run, 60_000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [walletAddr, log]);

  // ── Positions (every 30 s) ─────────────────────────────────────────────────
  useEffect(() => {
    if (!walletAddr) return;
    const run = async () => {
      setLoadingPos(true);
      try {
        const r    = await fetch(`https://data-api.polymarket.com/positions?user=${walletAddr}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const raw  = await r.json();
        // API may return array directly or { data: [...] }
        const data: any[] = Array.isArray(raw) ? raw : Array.isArray(raw?.data) ? raw.data : [];
        const mapped: Position[] = data
          .filter((i: any) => parseFloat(i?.currentValue ?? i?.value ?? 0) >= DUST_THRESHOLD)
          .map((i: any) => {
            const sz  = parseFloat(i?.size) || 1;

            // Cost basis: total dollars invested (NOT per-share). Try all known field names.
            const inv = parseFloat(i?.initialValue ?? i?.investedAmount ?? i?.costBasis ?? 0) || 0;

            // Current market value of the full position
            const cv  = parseFloat(i?.currentValue ?? i?.value ?? 0) || 0;

            // ── UNREALIZED PnL — computed locally, never trusted from API ────
            // BUG ROOT CAUSE: `i?.cashPnl ?? 0` defaults to 0 when the field is
            // missing/undefined. 0 >= 0 = true → every position rendered green.
            // FIX: compute directly from currentValue and cost basis.
            //   For YES shares:  PnL = currentValue − costBasis
            //   For NO  shares:  same formula (NO tokens have their own market price)
            // This can be negative → rendered red correctly.
            const unrealizedPnl = cv - inv;
            const unrealizedPct = inv > 0 ? ((cv - inv) / inv) * 100 : 0;

            return {
              id:           i?.assetId ?? i?.conditionId ?? Math.random().toString(36),
              title:        i?.title ?? i?.question ?? i?.market ?? 'Untitled Market',
              size:         i?.size ?? 0,
              currentValue: cv,
              cashPnl:      unrealizedPnl,
              percentPnl:   unrealizedPct,
              entryPrice:   sz > 0 ? inv / sz : 0,
              currentPrice: sz > 0 ? cv  / sz : 0,
              outcome:      i?.outcome ?? i?.side ?? '',
            };
          });
        setPositions(mapped);
        log(`RECV: ${mapped.length} positions (dust filtered)`);
      } catch {
        log('ERR: Positions fetch failed');
      } finally {
        setLoadingPos(false);
      }
    };
    run();
    const iv = setInterval(run, 30_000);
    return () => clearInterval(iv);
  }, [walletAddr, log]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── PNL Activity — lazy-load on first tab visit, cached per wallet ────────
  useEffect(() => {
    if (activeTab !== 'pnl' || pnlFetched || !walletAddr) return;
    let cancelled = false;
    (async () => {
      setLoadingPnl(true);
      try {
        // Fetch up to 500 activity records; on partial failure return safe []
        const url = `https://data-api.polymarket.com/activity?user=${walletAddr}&limit=500`;
        const r   = await fetch(url);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const raw = await r.json().catch(() => null);
        if (cancelled) return;
        const allActivities: any[] = Array.isArray(raw)
          ? raw
          : Array.isArray(raw?.data) ? raw.data : [];

        // ── Clean-Slate filter: keep only activity on/after PNL_CUTOFF_TIMESTAMP ──
        const activities = allActivities.filter((tx: any) => {
          try {
            const ts = tx.timestamp ?? tx.createdAt ?? tx.created_at ?? null;
            if (ts === null) return false;
            const ms = typeof ts === 'string'
              ? new Date(ts).getTime()
              : Number(ts) * (String(ts).length <= 10 ? 1000 : 1); // unix-s vs unix-ms
            return isFinite(ms) && ms > PNL_CUTOFF_TIMESTAMP; // strict: drop anything AT or before cutoff
          } catch { return false; }
        });

        // Run math engine — safe: returns [] on any internal error
        let trades: TradeRecord[] = [];
        try { trades = computeTradesFromActivity(activities); } catch { /* safe fallback */ }
        setTradePnls(trades);
        setPnlFetched(true);
        log(`PNL: ${allActivities.length} total, ${activities.length} post-cutoff → ${trades.length} closed trades`);
      } catch (err: any) {
        if (cancelled) return;
        log(`ERR: Activity fetch failed — ${err?.message ?? 'unknown'}`);
        setTradePnls([]);   // safe fallback: empty, not crash
        setPnlFetched(true);
      } finally {
        if (!cancelled) setLoadingPnl(false);
      }
    })();
    return () => { cancelled = true; };
  }, [activeTab, walletAddr, pnlFetched, log]);

  // ============================================================================
  // Tab: TERMINAL
  // ============================================================================
  const TabTerminal = () => (
    <div className="space-y-4">
      {/* Portfolio Overview */}
      {!walletAddr ? (
        <section className="border border-[#262626] p-5 bg-[#050505] text-center space-y-3">
          <p className="opacity-40 uppercase tracking-widest text-[10px]">No Wallet Configured</p>
          <button onClick={() => setActiveTab('settings')}
            className="text-[10px] uppercase tracking-widest border px-4 py-2"
            style={{ color: '#9fff88', borderColor: '#9fff88' }}>
            → Open Settings
          </button>
        </section>
      ) : (() => {
        // Derive accounting figures from live state — no extra fetch needed.
        // Total Value = Active Positions Value + USDC.e (Liquid) from Polygon RPC.
        const posValue = positions.reduce((s, p) => s + p.currentValue, 0);
        const usdc     = usdcBalance;                              // decoupled state
        const total    = usdc !== null ? usdc + posValue : null;
        return (
          <section className="border border-[#262626] bg-[#050505] p-4 space-y-3">
            <header className="flex justify-between items-center border-b border-[#1a1a1a] pb-2">
              <span className="uppercase tracking-widest opacity-50 text-[9px]">Portfolio Overview</span>
              <div className="flex items-center gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-[#9fff88] animate-pulse" />
                <span className="text-[8px]" style={{ color: '#9fff88' }}>LIVE · POLYGON RPC</span>
              </div>
            </header>
            <div className="grid grid-cols-2 gap-x-4 gap-y-3">
              {/* Total = USDC + Positions */}
              <div>
                <span className="block text-[8px] uppercase opacity-40 mb-0.5">Total Value</span>
                <span className="text-xl font-bold" style={{ color: '#9fff88' }}>
                  {total !== null ? fmt(total) : <span className="animate-pulse opacity-40">…</span>}
                </span>
              </div>
              {/* Liquid cash on-chain */}
              <div>
                <span className="block text-[8px] uppercase opacity-40 mb-0.5">USDC (Liquid)</span>
                <span className="text-xl font-bold text-white">
                  {usdc !== null ? fmt(usdc) : <span className="animate-pulse opacity-40">…</span>}
                </span>
              </div>
              {/* Positions value — sum of currentValue from Gamma API */}
              <div>
                <span className="block text-[8px] uppercase opacity-40 mb-0.5">Positions Value</span>
                <span className="text-sm" style={{ color: posValue > 0 ? '#9fff88' : '#ababab' }}>
                  {positions.length > 0 ? fmt(posValue) : '—'}
                </span>
              </div>
              {/* Count */}
              <div>
                <span className="block text-[8px] uppercase opacity-40 mb-0.5">Open Positions</span>
                <span className="text-sm" style={{ color: positions.length > 0 ? '#9fff88' : '#ababab' }}>
                  {loadingPos && positions.length === 0 ? '…' : positions.length}
                </span>
              </div>
            </div>
            {/* Phase 2a Model Confidence row */}
            <div className="border-t border-[#1a1a1a] pt-2 mt-1 grid grid-cols-2 gap-x-4 gap-y-1">
              <div>
                <span className="block text-[8px] uppercase opacity-40 mb-0.5">LON σ (Confidence)</span>
                <span className="text-[11px] font-mono" style={{ color: '#9fff88' }}>
                  {sigmaLon.toFixed(1)}°C
                </span>
              </div>
              <div>
                <span className="block text-[8px] uppercase opacity-40 mb-0.5">SEO σ (Confidence)</span>
                <span className="text-[11px] font-mono" style={{ color: '#9fff88' }}>
                  {sigmaSeo.toFixed(1)}°C
                </span>
              </div>
            </div>
          </section>
        );
      })()}

      {/* ── Phase 2 Dataset Collection Widget ────────────────────────────── */}
      {/* Sourced from S3 trades.jsonl, version="v2-bias-patch-stable" + event="ENTRY" */}
      {(() => {
        const PHASE2_TARGET = 300;
        const pct           = Math.min((phase2Count / PHASE2_TARGET) * 100, 100);
        // Colour shifts: grey → amber → green as dataset fills
        const barColour = pct >= 80 ? '#9fff88' : pct >= 40 ? '#f59e0b' : '#6b6b6b';
        const glowColour = pct >= 80
          ? 'rgba(159,255,136,0.45)'
          : pct >= 40 ? 'rgba(245,158,11,0.35)' : 'rgba(107,107,107,0.2)';

        return (
          <section className="border border-[#262626] bg-[#050505] p-4 space-y-3">
            {/* Header row */}
            <div className="flex justify-between items-center border-b border-[#1a1a1a] pb-2">
              <span className="uppercase tracking-widest opacity-50 text-[9px]">Phase 2 Dataset</span>
              <div className="flex items-center gap-1.5">
                <div className="w-1.5 h-1.5 rounded-full animate-pulse"
                  style={{ background: barColour }} />
                <span className="text-[8px] font-mono uppercase"
                  style={{ color: barColour }}>
                  {pct >= 100 ? 'COMPLETE' : 'COLLECTING'}
                </span>
              </div>
            </div>

            {/* Entry count + progress bar */}
            <div>
              <div className="flex justify-between items-end mb-2">
                <span className="text-[8px] uppercase opacity-40">Entries Tagged</span>
                <span className="font-mono text-[11px] font-bold" style={{ color: barColour }}>
                  {phase2Count}&nbsp;
                  <span className="opacity-40 font-normal">/ {PHASE2_TARGET}</span>
                </span>
              </div>
              {/* Progress track */}
              <div className="h-[3px] rounded-full bg-[#1a1a1a] overflow-hidden">
                <div
                  className="h-full rounded-full"
                  style={{
                    width:      `${pct}%`,
                    background: `linear-gradient(90deg, ${barColour} 0%, ${barColour}cc 100%)`,
                    boxShadow:  `0 0 8px ${glowColour}`,
                    transition: 'width 0.7s ease',
                  }}
                />
              </div>
              <div className="flex justify-between mt-1">
                <span className="text-[7px] opacity-20 font-mono">0</span>
                <span className="text-[7px] opacity-20 font-mono">{PHASE2_TARGET}</span>
              </div>
            </div>

            {/* Avg edge + version tag */}
            <div className="flex justify-between items-center">
              <div>
                <span className="block text-[7px] uppercase opacity-30 mb-0.5">Avg Edge</span>
                <span className="font-mono text-[13px] font-bold"
                  style={{ color: phase2Count > 0 ? '#9fff88' : '#3a3a3a' }}>
                  {phase2Count > 0
                    ? `+${(phase2Edge * 100).toFixed(2)}%`
                    : '—'}
                </span>
              </div>
              <span className="text-[7px] font-mono px-1.5 py-0.5 border border-[#1a1a1a] opacity-30">
                v2-bias-patch-stable
              </span>
            </div>
          </section>
        );
      })()}

      {/* London EV Map — sigma from Phase 2a calibration matrix */}
      <InferenceChart city="LONDON SHADOW NODE" code="LON"
        forecast={fcastLon} map={mapLon} sigma={sigmaLon} />

      {/* Seoul EV Map — sigma from Phase 2a calibration matrix */}
      <InferenceChart city="SEOUL SHADOW NODE" code="SEO"
        forecast={fcastSeo} map={mapSeo} sigma={sigmaSeo} />

      {/* Event Log */}
      <section className="border border-[#262626] bg-[#020202] p-3 flex flex-col" style={{ height: '9rem' }}>
        <header className="text-[8px] uppercase tracking-widest opacity-40 mb-2 border-b border-[#1a1a1a] pb-1 flex justify-between">
          <span>Engine Event Log</span>
          <div className="w-1 h-1 rounded-full bg-[#9fff88] animate-pulse" />
        </header>
        <div className="flex-grow overflow-y-auto space-y-0.5 font-mono text-[9px]">
          {[...logs].reverse().map((l, i) => (
            <div key={i} className="whitespace-nowrap overflow-hidden text-ellipsis border-l border-[#262626] pl-2">
              <span style={{
                color: l.includes('ERR') ? '#ff706f' : l.includes('WARN') ? '#f59e0b' :
                       l.includes('SUCC') || l.includes('METEO') || l.includes('INF') || l.includes('RECV') ? '#9fff88' : '#6b6b6b'
              }}>{l}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );

  // ============================================================================
  // Tab: PNL & METRICS
  // ============================================================================
  const TabPnl = () => {
    const total    = tradePnls.length;
    const wins     = tradePnls.filter(t => t.isWin).length;
    const losses   = tradePnls.filter(t => !t.isWin).length;
    const totalPnl = tradePnls.reduce((s, t) => s + t.pnl, 0);
    const sumWins  = tradePnls.filter(t => t.isWin).reduce((s, t) => s + t.pnl, 0);
    const sumLoss  = tradePnls.filter(t => !t.isWin).reduce((s, t) => s + Math.abs(t.pnl), 0);
    const winRate  = total > 0 ? (wins / total) * 100 : 0;
    const profFact = sumLoss > 0 ? sumWins / sumLoss : (sumWins > 0 ? Infinity : 0);
    const mean     = total > 0 ? totalPnl / total : 0;
    const variance = total > 1 ? tradePnls.reduce((s, t) => s + Math.pow(t.pnl - mean, 2), 0) / (total - 1) : 0;
    const sharpe   = variance > 0 ? mean / Math.sqrt(variance) : 0;

    return (
      <div className="space-y-4">
        <div className="flex justify-between items-center px-1">
          <span className="uppercase tracking-widest text-[10px]" style={{ color: '#9fff88' }}>
            PnL &amp; Metrics
          </span>
          <div className="flex items-center gap-2">
            {loadingPnl && <span className="text-[8px] animate-pulse" style={{ color: '#9fff88' }}>LOADING…</span>}
            {pnlFetched && !loadingPnl && (
              <span className="text-[8px] opacity-40">{total} CLOSED TRADES</span>
            )}
          </div>
        </div>

        {!walletAddr ? (
          <div className="border border-[#262626] p-6 bg-[#050505] text-center space-y-3">
            <p className="opacity-40 uppercase tracking-widest text-[10px]">No Wallet Configured</p>
            <button onClick={() => setActiveTab('settings')}
              className="text-[10px] uppercase tracking-widest border px-4 py-2"
              style={{ color: '#9fff88', borderColor: '#9fff88' }}>
              → Open Settings
            </button>
          </div>
        ) : loadingPnl && !pnlFetched ? (
          <div className="border border-[#262626] p-8 bg-[#050505] text-center">
            <span className="text-[10px] uppercase tracking-widest animate-pulse" style={{ color: '#9fff88' }}>
              Computing shadow P&amp;L…
            </span>
          </div>
        ) : (
          <>
            {/* KPI Grid — 2×3 */}
            <section className="border border-[#262626] bg-[#050505] p-4">
              <header className="flex justify-between items-center border-b border-[#1a1a1a] pb-2 mb-3">
                <span className="uppercase tracking-widest opacity-50 text-[9px]">Realized Performance</span>
                <div className="flex items-center gap-1">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#9fff88] animate-pulse" />
                  <span className="text-[8px]" style={{ color: '#9fff88' }}>LOCAL MATH ENGINE</span>
                </div>
              </header>
              <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                {/* Realized PnL — shows $0.00 on clean-slate, never NaN */}
                <div>
                  <span className="block text-[8px] uppercase opacity-40 mb-0.5">Realized PnL</span>
                  <span className="text-xl font-bold" style={{ color: total === 0 ? '#ababab' : totalPnl >= 0 ? '#9fff88' : '#ff706f' }}>
                    {total === 0 ? '$0.00' : `${totalPnl >= 0 ? '+' : ''}${fmt(totalPnl)}`}
                  </span>
                </div>
                {/* Win Rate */}
                <div>
                  <span className="block text-[8px] uppercase opacity-40 mb-0.5">Win Rate</span>
                  <span className="text-xl font-bold" style={{ color: total === 0 ? '#ababab' : winRate >= 50 ? '#9fff88' : '#ff706f' }}>
                    {total === 0 ? '0%' : `${winRate.toFixed(1)}%`}
                  </span>
                </div>
                {/* Profit Factor */}
                <div>
                  <span className="block text-[8px] uppercase opacity-40 mb-0.5">Profit Factor</span>
                  <span className="text-sm font-bold" style={{ color: '#ababab' }}>
                    {total === 0 ? '—' : profFact === Infinity ? '∞' : profFact.toFixed(2)}
                  </span>
                </div>
                {/* Sharpe */}
                <div>
                  <span className="block text-[8px] uppercase opacity-40 mb-0.5">Sharpe Ratio</span>
                  <span className="text-sm font-bold" style={{ color: '#ababab' }}>
                    {total < 2 ? '—' : (isFinite(sharpe) ? sharpe.toFixed(3) : '—')}
                  </span>
                </div>
                {/* Total Trades */}
                <div>
                  <span className="block text-[8px] uppercase opacity-40 mb-0.5">Total Trades</span>
                  <span className="text-sm" style={{ color: '#ababab' }}>{total}</span>
                </div>
                {/* W / L */}
                <div>
                  <span className="block text-[8px] uppercase opacity-40 mb-0.5">W / L</span>
                  <span className="text-sm font-mono">
                    <span style={{ color: '#9fff88' }}>{wins}</span>
                    <span className="opacity-30"> / </span>
                    <span style={{ color: '#ff706f' }}>{losses}</span>
                  </span>
                </div>
              </div>
            </section>

            {/* Clean-slate empty state — shown when fetch done but zero trades in window */}
            {total === 0 && pnlFetched && (
              <section className="border border-[#262626] bg-[#020202] p-5 text-center space-y-2">
                <div className="w-2 h-2 rounded-full bg-[#9fff88] mx-auto animate-pulse" />
                <p className="text-[9px] uppercase tracking-widest opacity-40">Clean Slate Active</p>
                <p className="text-[8px] opacity-25">No closed trades after cutoff</p>
                <p className="text-[9px] font-mono" style={{ color: '#9fff88' }}>$0.00 · 0 Trades</p>
              </section>
            )}

            {/* PnL bar chart — last 20 closed trades */}
            <section className="border border-[#262626] bg-[#020202] overflow-hidden p-3">
              <header className="text-[8px] uppercase tracking-widest opacity-40 mb-2 border-b border-[#1a1a1a] pb-1 flex justify-between">
                <span>Last {Math.min(total, 20)} Closed Trades</span>
                <span className="font-mono" style={{ color: totalPnl >= 0 ? '#9fff88' : '#ff706f' }}>
                  {total > 0 ? `${totalPnl >= 0 ? '+' : ''}${fmt(totalPnl)}` : ''}
                </span>
              </header>
              <PnlChart trades={tradePnls} />
            </section>

            {/* Trade log — newest first, max 15 rows */}
            {total > 0 && (
              <section className="border border-[#262626] bg-[#050505] divide-y divide-[#111]">
                <div className="grid grid-cols-[1fr_auto] px-3 py-1 border-b border-[#1a1a1a]">
                  <span className="text-[8px] uppercase opacity-30">Market</span>
                  <span className="text-[8px] uppercase opacity-30">Realized</span>
                </div>
                {[...tradePnls].reverse().slice(0, 15).map((t, i) => (
                  <div key={i} className="grid grid-cols-[1fr_auto] gap-2 px-3 py-2 items-center">
                    <span className="text-[9px] opacity-50 truncate">{t.market}</span>
                    <span className="text-[10px] font-mono font-bold shrink-0" style={{ color: t.isWin ? '#9fff88' : '#ff706f' }}>
                      {t.pnl >= 0 ? '+' : ''}{fmt(t.pnl)}
                    </span>
                  </div>
                ))}
              </section>
            )}

            {/* Refresh button — re-fetch on demand */}
            <button onClick={() => { setPnlFetched(false); setTradePnls([]); }}
              className="w-full py-2 text-[10px] uppercase tracking-widest border"
              style={{ color: '#9fff88', borderColor: '#262626' }}>
              ↺ Refresh Activity
            </button>
          </>
        )}
      </div>
    );
  };

  // ============================================================================
  // Tab: POSITIONS
  // ============================================================================
  const TabPositions = () => (
    <div className="space-y-3">
      <div className="flex justify-between items-center px-1">
        <span className="uppercase tracking-widest text-[10px]" style={{ color: '#9fff88' }}>
          Active Positions
        </span>
        <span className="text-[9px] opacity-40">{positions.length} OPEN</span>
      </div>

      {!walletAddr ? (
        <div className="border border-[#262626] p-6 bg-[#050505] text-center space-y-3">
          <p className="opacity-40 uppercase tracking-widest text-[10px]">No Wallet Configured</p>
          <button onClick={() => setActiveTab('settings')}
            className="text-[10px] uppercase tracking-widest border px-4 py-2"
            style={{ color: '#9fff88', borderColor: '#9fff88' }}>
            → Open Settings
          </button>
        </div>
      ) : loadingPos && positions.length === 0 ? (
        <div className="border border-[#262626] p-6 bg-[#050505] text-center">
          <span className="text-[10px] uppercase tracking-widest animate-pulse" style={{ color: '#9fff88' }}>
            Syncing positions…
          </span>
        </div>
      ) : positions.length === 0 ? (
        <div className="border border-[#262626] p-6 bg-[#050505] text-center opacity-40 uppercase tracking-widest text-[10px]">
          No active positions
        </div>
      ) : (
        <div className="border border-[#262626] bg-[#050505] divide-y divide-[#111]">
          {positions.map(pos => {
            const profit      = pos.cashPnl >= 0;
            const pnlClr      = profit ? '#9fff88' : '#ff706f';
            const outcome     = pos.outcome?.toUpperCase() || (profit ? 'YES' : 'NO');
            const isYes       = outcome === 'YES';
            return (
              <div key={pos.id} className="p-3 space-y-2">
                {/* Title + badge */}
                <div className="flex justify-between items-start gap-2">
                  <span className="font-bold text-white text-[10px] uppercase leading-snug flex-1 min-w-0 truncate">
                    {pos.title}
                  </span>
                  <span className={`shrink-0 text-[8px] px-1.5 py-0.5 font-bold ${isYes ? 'bg-[#9fff88] text-black' : 'bg-[#ff706f] text-black'}`}>
                    {outcome}
                  </span>
                </div>
                {/* Grid stats */}
                <div className="grid grid-cols-3 gap-2 text-[9px]">
                  <div>
                    <span className="block opacity-40 uppercase text-[7px] mb-0.5">Contracts</span>
                    <span>{parseFloat(pos.size as string).toFixed(2)}</span>
                  </div>
                  <div>
                    <span className="block opacity-40 uppercase text-[7px] mb-0.5">Entry → Now</span>
                    <span>{pos.entryPrice.toFixed(3)} → {pos.currentPrice.toFixed(3)}</span>
                  </div>
                  <div className="text-right">
                    <span className="block opacity-40 uppercase text-[7px] mb-0.5">Value</span>
                    <span className="text-white">{fmt(pos.currentValue)}</span>
                  </div>
                </div>
                {/* PnL row — edgeEntry slot inserted when S3 provides it */}
                <div className="flex justify-between items-center text-[9px]">
                  <span style={{ color: pnlClr }}>
                    PnL {profit ? '+' : ''}{fmt(pos.cashPnl)}
                  </span>
                  {pos.edgeEntry !== undefined && (
                    <span style={{ color: '#6b6b6b' }}>
                      EDGE {pos.edgeEntry >= 0 ? '+' : ''}{(pos.edgeEntry * 100).toFixed(1)}%
                    </span>
                  )}
                  <span style={{ color: pnlClr }}>
                    ROI {pct(pos.percentPnl)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  // ============================================================================
  // Tab: SETTINGS
  // ============================================================================
  const TabSettings = () => (
    <div className="space-y-4">
      {/* Wallet */}
      <div className="border border-[#262626] bg-[#050505] p-4 space-y-3">
        <header className="border-b border-[#1a1a1a] pb-2">
          <span className="uppercase tracking-widest text-[9px] opacity-50">Wallet Address</span>
        </header>
        <label className="block text-[8px] uppercase opacity-40">Polymarket Funder Address (0x…)</label>
        <input type="text" value={walletInput} onChange={e => setWalletInput(e.target.value)}
          placeholder="0x..."
          className="w-full bg-[#0a0a0a] border border-[#262626] text-[10px] font-mono px-3 py-2 text-white"
          style={{ letterSpacing: '0.04em' }} />
        <button onClick={() => {
          const a = walletInput.trim() || DEFAULT_WALLET;
          setWalletAddr(a);
          setWalletInput(a);
          if (typeof window !== 'undefined') localStorage.setItem(LS_WALLET_KEY, a);
          setTradePnls([]); setPnlFetched(false); // reset PNL cache for new wallet
          log(`CFG: Wallet → ${a.slice(0,6)}…${a.slice(-4)}`);
        }}
          className="w-full py-2 text-[10px] uppercase tracking-widest font-bold border"
          style={{ color: '#9fff88', borderColor: '#9fff88' }}>
          Save Wallet
        </button>
      </div>

      {/* System status */}
      <div className="border border-[#262626] bg-[#050505] p-4 space-y-2">
        <header className="border-b border-[#1a1a1a] pb-2 mb-3">
          <span className="uppercase tracking-widest text-[9px] opacity-50">System Status</span>
        </header>
        {([
          ['Wallet',       walletAddr ? `${walletAddr.slice(0,6)}…${walletAddr.slice(-4)}` : 'NOT SET',  !!walletAddr],
          ['LON Forecast', fcastLon !== null ? `${fcastLon.toFixed(1)}°C` : 'PENDING',               fcastLon !== null],
          ['SEO Forecast', fcastSeo !== null ? `${fcastSeo.toFixed(1)}°C` : 'PENDING',               fcastSeo !== null],
          ['Positions',    `${positions.length} active`,                                               positions.length > 0],
          ['Activity',     pnlFetched ? `${tradePnls.length} trades` : 'NOT LOADED',                  pnlFetched],
          ['Data Source',  positions.length > 0 && positions[0]?.edgeEntry !== undefined ? 'S3 LIVE' : 'POLYMARKET', positions.length > 0],
        ] as [string, string, boolean][]).map(([lbl, val, ok]) => (
          <div key={lbl} className="flex justify-between items-center text-[10px]">
            <span className="opacity-40 uppercase">{lbl}</span>
            <span className="font-mono" style={{ color: ok ? '#9fff88' : '#ff706f' }}>{val}</span>
          </div>
        ))}
      </div>

      {/* Clear data */}
      <div className="border border-[#262626] bg-[#050505] p-4">
        <button onClick={() => {
          if (typeof window !== 'undefined') localStorage.removeItem(LS_WALLET_KEY);
          setWalletAddr(DEFAULT_WALLET); setWalletInput(DEFAULT_WALLET);
          setTradePnls([]); setPnlFetched(false);
          log('CFG: Credentials reset to defaults');
        }}
          className="w-full py-2 text-[10px] uppercase tracking-widest border"
          style={{ color: '#ff706f', borderColor: '#ff706f' }}>
          Clear All Stored Data
        </button>
      </div>
    </div>
  );

  // ============================================================================
  // Nav config
  // ============================================================================
  const NAV: { tab: ActiveTab; label: string; path: string }[] = [
    { tab: 'terminal',  label: 'TERMINAL',
      path: 'M4 4h7v7H4V4zm0 12h7v7H4v-7zm12-12h7v7h-7V4zm0 12h7v7h-7v-7z' },
    { tab: 'pnl',       label: 'PNL',
      path: 'M4 9h4v11H4V9zm6-5h4v16h-4V4zm6 8h4v8h-4v-8z' },
    { tab: 'positions', label: 'POSITIONS',
      path: 'M16 6l2.29 2.29-4.88 4.88-4-4L2 16.59 3.41 18l6-6 4 4 6.3-6.29L22 12V6h-6z' },
    { tab: 'settings',  label: 'SETTINGS',
      path: 'M19.43 12.98c.04-.32.07-.64.07-.98s-.03-.66-.07-.98l2.11-1.65c.19-.15.24-.42.12-.64l-2-3.46c-.12-.22-.39-.3-.61-.22l-2.49 1c-.52-.4-1.08-.73-1.69-.98l-.38-2.65C14.46 2.18 14.25 2 14 2h-4c-.25 0-.46.18-.49.42l-.38 2.65c-.61.25-1.17.59-1.69.98l-2.49-1c-.23-.09-.49 0-.61.22l-2 3.46c-.13.22-.07.49.12.64l2.11 1.65c-.04.32-.07.65-.07.98s.03.66.07.98l-2.11 1.65c-.19.15-.24.42-.12.64l2 3.46c.12.22.39.3.61.22l2.49-1c.52.4 1.08.73 1.69.98l.38 2.65c.03.24.24.42.49.42h4c.25 0 .46-.18.49-.42l.38-2.65c.61-.25 1.17-.59 1.69-.98l2.49 1c.23.09.49 0 .61-.22l2-3.46c.12-.22.07-.49-.12-.64l-2.11-1.65zM12 15.5c-1.93 0-3.5-1.57-3.5-3.5s1.57-3.5 3.5-3.5 3.5 1.57 3.5 3.5-1.57 3.5-3.5 3.5z' },
  ];

  // ============================================================================
  // Hydration guard — server and first client render return this identical
  // loading screen. Once the client mounts, isMounted flips to true and the
  // full app renders. Eliminates React Error #418 (hydration mismatch).
  // IMPORTANT: this must come AFTER every useState / useEffect call above.
  // ============================================================================
  if (!isMounted) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <span className="font-mono text-sm tracking-widest animate-pulse"
          style={{ color: '#9fff88' }}>
          [ INITIALIZING LARIANT ENGINE... ]
        </span>
      </div>
    );
  }

  // ============================================================================
  // Render
  // ============================================================================
  return (
    <div className="min-h-screen bg-black text-[#ababab] font-mono text-[10px] sm:text-xs selection:bg-[#9fff88] selection:text-black leading-tight overflow-x-hidden">
      {/* CRT Scanline Overlay */}
      <div className={`fixed inset-0 pointer-events-none z-50 opacity-[0.03] bg-[linear-gradient(rgba(18,16,16,0)_50%,rgba(0,0,0,0.25)_50%),linear-gradient(90deg,rgba(255,0,0,0.06),rgba(0,255,0,0.02),rgba(0,0,255,0.06))] bg-[length:100%_2px,3px_100%]`} />

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      {/*
        ── App Header — iPhone 15 / Dynamic Island safe ──────────────────────
        The outer <header> absorbs env(safe-area-inset-top) as top padding so
        the bar background fills the status-bar area while the inner content
        row sits *below* the Dynamic Island / notch.  h-11 is kept on the
        inner row so the content height is consistent across devices.
      */}
      <header
        className="border-b border-[#262626] bg-[#0a0a0a] sticky top-0 z-40"
        style={{ paddingTop: 'env(safe-area-inset-top, 0px)' }}
      >
        <div className="flex justify-between items-center px-3 h-11">
          <div className="flex items-center gap-2">
            {/* Use branded PWA icon (green mark on black) instead of raw JPG */}
            <img src="/lariant-icon-192.png" alt="LARIANT"
              className="w-7 h-7 rounded-full object-cover shrink-0"
              onError={(e: any) => { e.currentTarget.style.display = 'none'; }} />
            <span className="font-bold tracking-widest text-xs drop-shadow-[0_0_6px_rgba(159,255,136,0.5)]"
              style={{ color: '#9fff88' }}>
              LARIANT
            </span>
            <div className="w-1.5 h-1.5 rounded-full bg-[#9fff88] animate-pulse" />
          </div>
          <span className="text-[8px] uppercase tracking-tighter opacity-40">
            {walletAddr ? `${walletAddr.slice(0,4)}…${walletAddr.slice(-4)}` : 'NO WALLET'} | {clock}
          </span>
        </div>
      </header>

      {/* ── Main scrollable area ────────────────────────────────────────────── */}
      <main className="p-3 pb-36 space-y-4 max-w-3xl mx-auto">
        {activeTab === 'terminal'  && <TabTerminal />}
        {activeTab === 'pnl'       && <TabPnl />}
        {activeTab === 'positions' && <TabPositions />}
        {activeTab === 'settings'  && <TabSettings />}
      </main>

      {/* ── Bottom Nav Bar — safe-area aware, iPhone home-indicator proof ───── */}
      <nav className="fixed bottom-0 left-0 right-0 bg-[#050505] border-t border-[#262626] flex justify-around items-end z-40 px-1"
        style={{ paddingTop: '0.625rem', paddingBottom: 'max(env(safe-area-inset-bottom, 1rem), 1rem)' }}>
        {NAV.map(({ tab, label, path }) => {
          const active = activeTab === tab;
          return (
            <button key={tab} onClick={() => setActiveTab(tab)}
              className="flex flex-col items-center gap-1.5 pb-1 transition-all min-w-[64px]"
              style={{ color: active ? '#9fff88' : '#3a3a3a' }}>
              {/* Active indicator pill */}
              <div className="w-6 h-[2px] rounded-full mb-0.5 transition-all"
                style={{ background: active ? '#9fff88' : 'transparent', boxShadow: active ? '0 0 6px #9fff88' : 'none' }} />
              <svg className="w-7 h-7 fill-current transition-all"
                style={{ filter: active ? 'drop-shadow(0 0 5px rgba(159,255,136,0.7))' : 'none' }}
                viewBox="0 0 24 24">
                <path d={path} />
              </svg>
              <span className="text-[10px] font-bold tracking-widest uppercase">{label}</span>
            </button>
          );
        })}
      </nav>

      {/* ── Global CSS ─────────────────────────────────────────────────────── */}
      <style jsx global>{`
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;700&display=swap');

        body {
          background-color: #000;
          cursor: crosshair;
          -webkit-tap-highlight-color: transparent;
          overscroll-behavior-y: none;
          /* iOS momentum scroll on the full page */
          -webkit-overflow-scrolling: touch;
        }

        * { -webkit-font-smoothing: antialiased; box-sizing: border-box; }

        input, button { font-family: inherit; }
        input::placeholder { color: #333; }
        input:focus { outline: none; }

        @keyframes ev-pulse {
          0%, 100% { opacity: 1;   transform: scale(1);   }
          50%       { opacity: 0.4; transform: scale(1.8); }
        }
        .ev-pulse-dot {
          animation:        ev-pulse 1.5s ease-in-out infinite;
          transform-box:    fill-box;
          transform-origin: center center;
          will-change:      transform, opacity;
        }
      `}</style>
    </div>
  );
}
