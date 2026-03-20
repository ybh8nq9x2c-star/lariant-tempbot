"use client";
import React, { useState, useEffect, useCallback } from 'react';
import {
  ComposedChart, Bar, Area, Cell,
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
}

interface BucketData {
  bucket: string;
  modelProb: number;
  marketProb: number;
  hasLiveData: boolean;
}

interface OrderEntry {
  price: number;
  size: number;
}

interface OrderBookState {
  bids: OrderEntry[];
  asks: OrderEntry[];
  lastUpdated: Date | null;
}

type ActiveTab = 'terminal' | 'orderbook' | 'positions' | 'settings';

// ============================================================================
// Constants
// ============================================================================
const SIGMA_LON       = 2.2;
const SIGMA_SEO       = 2.8;
const NEUTRAL_FALLBACK = 0.15;
const EV_THRESHOLD    = 0.05;
const DUST_THRESHOLD  = 0.10;
const LS_WALLET_KEY   = 'lariant_wallet';
const LS_TOKEN_KEY    = 'lariant_token_id';
const DEFAULT_WALLET  = '0x3a6Fe25c5320dC81F35F0f4e6bE8bDc76BC5e1c7';

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

function buildBuckets(mu: number, sigma: number, positions: Position[]): BucketData[] {
  const base = Math.floor(mu) - 1;
  const p0   = normalCDF(base,     mu, sigma);
  const p1   = normalCDF(base + 1, mu, sigma);
  const p2   = normalCDF(base + 2, mu, sigma);
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
  return (
    <section className="border border-[#262626] bg-[#020202] relative overflow-hidden" style={{ height: '13rem' }}>
      <div className="absolute inset-0 flex flex-col p-3">
        {/* Header */}
        <div className="flex justify-between items-center shrink-0 mb-1">
          <span className="text-[8px] uppercase tracking-widest" style={{ color: '#9fff88', opacity: 0.7 }}>
            {city} <span className="opacity-60">σ={sigma}°C</span>
          </span>
          <span className="text-[8px] uppercase tracking-widest" style={{ color: '#9fff88' }}>
            {forecast !== null ? `T+1: ${forecast.toFixed(1)}°C` : `FETCHING ${code}…`}
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
        {/* Chart — explicit px height required: Recharts cannot measure flex-grow at hydration */}
        <div style={{ height: 140, overflow: 'hidden' }}>
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
                <Tooltip content={<EvTooltip />} cursor={{ stroke: '#2a2a2a', strokeWidth: 1 }} />
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
// fetchUsdcBalance — reads USDC.e balance directly from Polygon via public RPC
// No API key required. USDC.e contract: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
// balanceOf(address) selector: 0x70a08231, padded to 32 bytes
// ============================================================================
async function fetchUsdcBalance(walletAddress: string): Promise<number> {
  if (!walletAddress || walletAddress.length !== 42) return 0;
  // Strip 0x prefix and left-pad to 32 bytes for the ABI-encoded address param
  const paddedAddr = walletAddress.slice(2).toLowerCase().padStart(64, '0');
  const payload = {
    jsonrpc: '2.0',
    id:      1,
    method:  'eth_call',
    params:  [
      {
        to:   '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174', // USDC.e on Polygon
        data: '0x70a08231' + paddedAddr,                    // balanceOf(address)
      },
      'latest',
    ],
  };
  try {
    const r = await fetch('https://polygon-rpc.com', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });
    if (!r.ok) return 0;
    const d = await r.json();
    if (!d?.result || d.result === '0x') return 0;
    // USDC.e has 6 decimals
    return parseInt(d.result, 16) / 1_000_000;
  } catch {
    return 0;
  }
}

// ============================================================================
// Main Component
// ============================================================================
export default function LariantTerminal() {
  // ── State ─────────────────────────────────────────────────────────────────
  const [activeTab,    setActiveTab]    = useState<ActiveTab>('terminal');
  const [walletAddr,   setWalletAddr]   = useState(DEFAULT_WALLET);
  const [walletInput,  setWalletInput]  = useState(DEFAULT_WALLET);
  const [positions,    setPositions]    = useState<Position[]>([]);
  // usdc = on-chain USDC.e balance; posValue + total are derived in render from positions
  const [portfolio,    setPortfolio]    = useState<{ usdc: number } | null>(null);
  const [mapLon,       setMapLon]       = useState<BucketData[]>([]);
  const [mapSeo,       setMapSeo]       = useState<BucketData[]>([]);
  const [fcastLon,     setFcastLon]     = useState<number | null>(null);
  const [fcastSeo,     setFcastSeo]     = useState<number | null>(null);
  const [orderbook,    setOrderbook]    = useState<OrderBookState>({ bids: [], asks: [], lastUpdated: null });
  const [tokenId,      setTokenId]      = useState('');
  const [tokenInput,   setTokenInput]   = useState('');
  const [loadingPos,   setLoadingPos]   = useState(false);
  const [clock,        setClock]        = useState('');
  const [logs,         setLogs]         = useState<string[]>([
    `[${new Date().toLocaleTimeString()}] SYS: Booting LARIANT v1.0...`,
    `[${new Date().toLocaleTimeString()}] NET: Shadow Node initializing...`,
  ]);

  // ── Helpers ───────────────────────────────────────────────────────────────
  const log = useCallback((msg: string) => {
    setLogs(prev => [...prev.slice(-60), `[${new Date().toLocaleTimeString()}] ${msg}`]);
  }, []);

  // ── Bootstrap: load localStorage (safe — runs only client-side) ───────────
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const w = localStorage.getItem(LS_WALLET_KEY) || DEFAULT_WALLET;
    const t = localStorage.getItem(LS_TOKEN_KEY)  || '';
    setWalletAddr(w);  setWalletInput(w);
    if (t) { setTokenId(t); setTokenInput(t); }
  }, []);

  // ── Clock ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    const iv = setInterval(() => setClock(new Date().toISOString().slice(11, 19) + ' UTC'), 1000);
    return () => clearInterval(iv);
  }, []);

  // ── Weather: London 51.5048, 0.0495 ───────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch('https://api.open-meteo.com/v1/forecast?latitude=51.5048&longitude=0.0495&daily=temperature_2m_max&timezone=Europe%2FLondon&forecast_days=2');
        const d = await r.json();
        const t: number = d.daily.temperature_2m_max[1];
        setFcastLon(t);
        log(`METEO: LON T+1 MAX = ${t.toFixed(1)}°C (σ=${SIGMA_LON})`);
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
        log(`METEO: SEO T+1 MAX = ${t.toFixed(1)}°C (σ=${SIGMA_SEO})`);
      } catch {
        setFcastSeo(18.0);
        log('WARN: Open-Meteo SEO failed — fallback 18.0°C');
      }
    })();
  }, [log]);

  // ── EV Map: rebuild when forecast OR positions change ─────────────────────
  useEffect(() => {
    if (fcastLon === null) return;
    const b = buildBuckets(fcastLon, SIGMA_LON, positions);
    setMapLon(b);
    log(`INF: LON map rebuilt — ${b.filter(x => x.hasLiveData).length}/4 live prices`);
  }, [fcastLon, positions, log]);

  useEffect(() => {
    if (fcastSeo === null) return;
    const b = buildBuckets(fcastSeo, SIGMA_SEO, positions);
    setMapSeo(b);
    log(`INF: SEO map rebuilt — ${b.filter(x => x.hasLiveData).length}/4 live prices`);
  }, [fcastSeo, positions, log]);

  // ── USDC balance — Polygon RPC, auth-free, every 60 s ────────────────────
  useEffect(() => {
    if (!walletAddr || walletAddr.length !== 42) return;
    const run = async () => {
      const usdc = await fetchUsdcBalance(walletAddr);
      setPortfolio({ usdc });
      log(`RPC: USDC.e on-chain = ${fmt(usdc)}`);
    };
    run();
    const iv = setInterval(run, 60_000);
    return () => clearInterval(iv);
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
            const sz  = parseFloat(i?.size)         || 1;
            const inv = parseFloat(i?.initialValue ?? i?.avgPrice ?? 0) || 0;
            const cv  = parseFloat(i?.currentValue ?? i?.value ?? 0)    || 0;
            return {
              id:           i?.assetId ?? i?.conditionId ?? Math.random().toString(36),
              title:        i?.title ?? i?.question ?? i?.market ?? 'Untitled Market',
              size:         i?.size ?? 0,
              currentValue: cv,
              cashPnl:      parseFloat(i?.cashPnl   ?? i?.pnl   ?? 0) || 0,
              percentPnl:   parseFloat(i?.percentPnl ?? i?.pnlPct ?? 0) || 0,
              entryPrice:   sz > 0 ? inv / sz : 0,
              currentPrice: sz > 0 ? cv  / sz : 0,
              outcome:      i?.outcome ?? i?.side ?? '',
            };
          });
        setPositions(mapped);
        // Auto-detect CLOB token ID from first weather position
        if (!tokenId) {
          const wp = mapped.find((p: Position) => {
            const t = p.title.toLowerCase();
            return t.includes('temp') || t.includes('weather') || t.includes('°');
          });
          if (wp) { setTokenId(wp.id); setTokenInput(wp.id); }
        }
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

  // ── Orderbook CLOB (every 15 s — respectful of rate limits) ─────────────
  useEffect(() => {
    // Require a real Polymarket token ID: hex starting with 0x, at least 20 chars
    const tid = tokenId.trim();
    if (!tid || tid.length < 20) return;

    // Declare iv BEFORE run so the async body can call clearInterval(iv) on fatal errors
    let iv: ReturnType<typeof setInterval>;

    const run = async () => {
      try {
        const r = await fetch(`https://clob.polymarket.com/book?token_id=${tid}`);
        if (!r.ok) {
          if (r.status === 404) {
            // Token not found — this token will never be valid. Kill the loop
            // and clear the stored token so the user knows to re-configure.
            clearInterval(iv);
            log('WARN: CLOB 404 — token invalid, polling stopped');
            setTokenId('');
            setTokenInput('');
            if (typeof window !== 'undefined') localStorage.removeItem(LS_TOKEN_KEY);
          }
          // 429 = rate limited; other errors: back off silently, interval continues
          return;
        }
        const d = await r.json();
        const bids: OrderEntry[] = (Array.isArray(d?.bids) ? d.bids : [])
          .map((b: any) => ({ price: parseFloat(b?.price ?? 0), size: parseFloat(b?.size ?? 0) }))
          .filter((e: OrderEntry) => e.price > 0 && e.size > 0)
          .sort((a: OrderEntry, b: OrderEntry) => b.price - a.price)
          .slice(0, 10);
        const asks: OrderEntry[] = (Array.isArray(d?.asks) ? d.asks : [])
          .map((a: any) => ({ price: parseFloat(a?.price ?? 0), size: parseFloat(a?.size ?? 0) }))
          .filter((e: OrderEntry) => e.price > 0 && e.size > 0)
          .sort((a: OrderEntry, b: OrderEntry) => a.price - b.price)
          .slice(0, 10);
        setOrderbook({ bids, asks, lastUpdated: new Date() });
      } catch {
        // Network error — silent, interval continues, will retry next tick
      }
    };

    run();
    iv = setInterval(run, 15_000);
    return () => clearInterval(iv);
  }, [tokenId, log]); // eslint-disable-line react-hooks/exhaustive-deps

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
        // Derive accounting figures from live state — no extra fetch needed
        const posValue = positions.reduce((s, p) => s + p.currentValue, 0);
        const usdc     = portfolio?.usdc ?? null;
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
                <span className="block text-[8px] uppercase opacity-40 mb-0.5">USDC.e (Liquid)</span>
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
          </section>
        );
      })()}

      {/* London EV Map */}
      <InferenceChart city="LONDON SHADOW NODE" code="LON"
        forecast={fcastLon} map={mapLon} sigma={SIGMA_LON} />

      {/* Seoul EV Map */}
      <InferenceChart city="SEOUL SHADOW NODE" code="SEO"
        forecast={fcastSeo} map={mapSeo} sigma={SIGMA_SEO} />

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
  // Tab: ORDERBOOK
  // ============================================================================
  const TabOrderbook = () => {
    const { bids, asks, lastUpdated } = orderbook;
    const maxSz   = Math.max(...bids.map(b => b.size), ...asks.map(a => a.size), 1);
    const bestBid = bids[0]?.price ?? 0;
    const bestAsk = asks[0]?.price ?? 0;
    const spreadV = bestBid > 0 && bestAsk > 0 ? bestAsk - bestBid : null;

    return (
      <div className="space-y-3">
        <div className="flex justify-between items-center px-1">
          <span className="uppercase tracking-widest text-[10px]" style={{ color: '#9fff88' }}>
            Live CLOB — Orderbook
          </span>
          {lastUpdated && (
            <span className="text-[8px] opacity-40">{lastUpdated.toLocaleTimeString()}</span>
          )}
        </div>

        {!tokenId ? (
          <div className="border border-[#262626] p-6 bg-[#050505] text-center space-y-3">
            <p className="opacity-40 uppercase tracking-widest text-[10px]">No Token ID configured</p>
            <button onClick={() => setActiveTab('settings')}
              className="text-[10px] uppercase tracking-widest border px-4 py-2"
              style={{ color: '#9fff88', borderColor: '#9fff88' }}>
              → Configure in Settings
            </button>
          </div>
        ) : bids.length === 0 && asks.length === 0 ? (
          <div className="border border-[#262626] p-6 bg-[#050505] text-center">
            <span className="text-[10px] uppercase tracking-widest animate-pulse" style={{ color: '#9fff88' }}>
              Fetching CLOB data…
            </span>
          </div>
        ) : (
          <div className="border border-[#262626] bg-[#050505] overflow-hidden">
            {/* Spread bar */}
            <div className="flex justify-between items-center px-3 py-2 border-b border-[#1a1a1a] bg-[#0a0a0a]">
              <span className="text-[9px] opacity-40 uppercase">Spread</span>
              <span className="text-[11px] font-bold text-white font-mono">
                {spreadV !== null ? spreadV.toFixed(4) : '—'}
              </span>
              <span className="text-[9px] opacity-40 uppercase">
                {spreadV !== null && bestAsk > 0 ? `${((spreadV / bestAsk) * 100).toFixed(2)}%` : ''}
              </span>
            </div>
            {/* Col headers */}
            <div className="grid grid-cols-3 px-3 py-1 border-b border-[#1a1a1a]">
              <span className="text-[8px] uppercase opacity-30">Price</span>
              <span className="text-[8px] uppercase opacity-30 text-center">Depth</span>
              <span className="text-[8px] uppercase opacity-30 text-right">Size</span>
            </div>
            {/* Asks — reversed so tightest ask is at centre */}
            {[...asks].reverse().map((a, i) => (
              <div key={`ask-${i}`} className="relative grid grid-cols-3 px-3 py-[3px] items-center">
                <div className="absolute left-0 top-0 bottom-0 bg-[#ff706f] opacity-[0.07]"
                  style={{ width: `${(a.size / maxSz) * 100}%` }} />
                <span className="text-[10px] font-mono relative z-10" style={{ color: '#ff706f' }}>
                  {a.price.toFixed(4)}
                </span>
                <div className="flex justify-center relative z-10">
                  <div className="h-[2px] bg-[#ff706f] opacity-50 rounded"
                    style={{ width: `${(a.size / maxSz) * 60}%` }} />
                </div>
                <span className="text-[9px] font-mono opacity-60 text-right relative z-10">
                  {a.size.toFixed(0)}
                </span>
              </div>
            ))}
            {/* Mid-price */}
            <div className="flex justify-center items-center py-1 border-y border-[#262626] bg-[#080808]">
              <span className="text-[9px] font-mono text-white opacity-50">
                {bestBid > 0 && bestAsk > 0 ? `MID  ${((bestBid + bestAsk) / 2).toFixed(4)}` : '—'}
              </span>
            </div>
            {/* Bids */}
            {bids.map((b, i) => (
              <div key={`bid-${i}`} className="relative grid grid-cols-3 px-3 py-[3px] items-center">
                <div className="absolute left-0 top-0 bottom-0 bg-[#9fff88] opacity-[0.06]"
                  style={{ width: `${(b.size / maxSz) * 100}%` }} />
                <span className="text-[10px] font-mono relative z-10" style={{ color: '#9fff88' }}>
                  {b.price.toFixed(4)}
                </span>
                <div className="flex justify-center relative z-10">
                  <div className="h-[2px] bg-[#9fff88] opacity-50 rounded"
                    style={{ width: `${(b.size / maxSz) * 60}%` }} />
                </div>
                <span className="text-[9px] font-mono opacity-60 text-right relative z-10">
                  {b.size.toFixed(0)}
                </span>
              </div>
            ))}
          </div>
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
                {/* PnL row */}
                <div className="flex justify-between items-center text-[9px]">
                  <span style={{ color: pnlClr }}>
                    PnL {profit ? '+' : ''}{fmt(pos.cashPnl)}
                  </span>
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
          log(`CFG: Wallet → ${a.slice(0,6)}…${a.slice(-4)}`);
        }}
          className="w-full py-2 text-[10px] uppercase tracking-widest font-bold border"
          style={{ color: '#9fff88', borderColor: '#9fff88' }}>
          Save Wallet
        </button>
      </div>

      {/* CLOB Token */}
      <div className="border border-[#262626] bg-[#050505] p-4 space-y-3">
        <header className="border-b border-[#1a1a1a] pb-2">
          <span className="uppercase tracking-widest text-[9px] opacity-50">CLOB Market Token ID</span>
        </header>
        <label className="block text-[8px] uppercase opacity-40">
          Token ID — auto-detected from positions, or enter manually
        </label>
        <input type="text" value={tokenInput} onChange={e => setTokenInput(e.target.value)}
          placeholder="Token ID…"
          className="w-full bg-[#0a0a0a] border border-[#262626] text-[10px] font-mono px-3 py-2 text-white" />
        <button onClick={() => {
          const id = tokenInput.trim();
          setTokenId(id);
          if (typeof window !== 'undefined') localStorage.setItem(LS_TOKEN_KEY, id);
          log(`CFG: Token → ${id.slice(0,10)}…`);
        }}
          className="w-full py-2 text-[10px] uppercase tracking-widest font-bold border"
          style={{ color: '#9fff88', borderColor: '#9fff88' }}>
          Save Token ID
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
          ['CLOB Token',   tokenId ? `${tokenId.slice(0,10)}…` : 'NOT SET',                           !!tokenId],
          ['Orderbook',    orderbook.bids.length > 0 ? `${orderbook.bids.length}b/${orderbook.asks.length}a` : 'NO DATA', orderbook.bids.length > 0],
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
          if (typeof window !== 'undefined') {
            localStorage.removeItem(LS_WALLET_KEY);
            localStorage.removeItem(LS_TOKEN_KEY);
          }
          setWalletAddr(DEFAULT_WALLET); setWalletInput(DEFAULT_WALLET);
          setTokenId('');                setTokenInput('');
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
    { tab: 'orderbook', label: 'ORDERBOOK',
      path: 'M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z' },
    { tab: 'positions', label: 'POSITIONS',
      path: 'M16 6l2.29 2.29-4.88 4.88-4-4L2 16.59 3.41 18l6-6 4 4 6.3-6.29L22 12V6h-6z' },
    { tab: 'settings',  label: 'SETTINGS',
      path: 'M19.43 12.98c.04-.32.07-.64.07-.98s-.03-.66-.07-.98l2.11-1.65c.19-.15.24-.42.12-.64l-2-3.46c-.12-.22-.39-.3-.61-.22l-2.49 1c-.52-.4-1.08-.73-1.69-.98l-.38-2.65C14.46 2.18 14.25 2 14 2h-4c-.25 0-.46.18-.49.42l-.38 2.65c-.61.25-1.17.59-1.69.98l-2.49-1c-.23-.09-.49 0-.61.22l-2 3.46c-.13.22-.07.49.12.64l2.11 1.65c-.04.32-.07.65-.07.98s.03.66.07.98l-2.11 1.65c-.19.15-.24.42-.12.64l2 3.46c.12.22.39.3.61.22l2.49-1c.52.4 1.08.73 1.69.98l.38 2.65c.03.24.24.42.49.42h4c.25 0 .46-.18.49-.42l.38-2.65c.61-.25 1.17-.59 1.69-.98l2.49 1c.23.09.49 0 .61-.22l2-3.46c.12-.22.07-.49-.12-.64l-2.11-1.65zM12 15.5c-1.93 0-3.5-1.57-3.5-3.5s1.57-3.5 3.5-3.5 3.5 1.57 3.5 3.5-1.57 3.5-3.5 3.5z' },
  ];

  // ============================================================================
  // Render
  // ============================================================================
  return (
    <div className="min-h-screen bg-black text-[#ababab] font-mono text-[10px] sm:text-xs selection:bg-[#9fff88] selection:text-black leading-tight overflow-x-hidden">
      {/* CRT Scanline Overlay */}
      <div className={`fixed inset-0 pointer-events-none z-50 opacity-[0.03] bg-[linear-gradient(rgba(18,16,16,0)_50%,rgba(0,0,0,0.25)_50%),linear-gradient(90deg,rgba(255,0,0,0.06),rgba(0,255,0,0.02),rgba(0,0,255,0.06))] bg-[length:100%_2px,3px_100%]`} />

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="border-b border-[#262626] bg-[#0a0a0a] flex justify-between items-center px-3 h-11 sticky top-0 z-40">
        <div className="flex items-center gap-2">
          {/* Lariant logo — falls back gracefully if image missing */}
          <img src="/lariant-logo.jpg" alt=""
            className="w-7 h-7 rounded-full object-cover border border-[#262626] shrink-0"
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
      </header>

      {/* ── Main scrollable area ────────────────────────────────────────────── */}
      <main className="p-3 pb-24 space-y-4 max-w-3xl mx-auto">
        {activeTab === 'terminal'  && <TabTerminal />}
        {activeTab === 'orderbook' && <TabOrderbook />}
        {activeTab === 'positions' && <TabPositions />}
        {activeTab === 'settings'  && <TabSettings />}
      </main>

      {/* ── Bottom Nav Bar — safe-area aware ───────────────────────────────── */}
      <nav className="fixed bottom-0 left-0 right-0 bg-[#050505] border-t border-[#262626] flex justify-around items-center z-40 px-2"
        style={{ paddingTop: '0.5rem', paddingBottom: 'max(env(safe-area-inset-bottom), 0.625rem)' }}>
        {NAV.map(({ tab, label, path }) => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className="flex flex-col items-center gap-1 transition-all min-w-[56px]"
            style={{ color: activeTab === tab ? '#9fff88' : '#444' }}>
            <svg className={`w-5 h-5 fill-current ${activeTab === tab ? 'drop-shadow-[0_0_4px_rgba(159,255,136,0.6)]' : ''}`}
              viewBox="0 0 24 24">
              <path d={path} />
            </svg>
            <span className="text-[7px] font-bold tracking-tighter uppercase">{label}</span>
          </button>
        ))}
      </nav>

      {/* ── Global CSS ─────────────────────────────────────────────────────── */}
      <style jsx global>{`
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;700&display=swap');

        body {
          background-color: #000;
          cursor: crosshair;
          -webkit-tap-highlight-color: transparent;
          overscroll-behavior-y: none;
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
