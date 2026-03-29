/**
 * app/api/s3data/route.ts — LARIANT S3 Data API Route
 * ======================================================
 * Next.js App Router server-side route.  Runs in Node.js inside the same
 * Railway service as the Next.js app — no Python sidecar required.
 *
 * Uses @aws-sdk/client-s3 (pure Node, identical logic to s3_api.py/boto3).
 * Credentials are read EXCLUSIVELY from process.env — never from ~/.aws.
 *
 * Required env vars (Railway → Variables):
 *   AWS_ACCESS_KEY_ID
 *   AWS_SECRET_ACCESS_KEY
 *   AWS_DEFAULT_REGION      (default: us-east-1)
 *   S3_BUCKET
 *   S3_POSITIONS_KEY        (default: positions.json)
 *   S3_TRADES_KEY           (default: trades.jsonl)
 *
 * Optional — set to use the Python s3_api.py sidecar instead of this route:
 *   S3_API_URL   e.g. http://localhost:8000
 *
 * Response schema (mirrors s3_api.py):
 *   { s3Available, positions, sigmaLon, sigmaSeo }
 */

import { NextResponse }    from 'next/server';
import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3';

// ── Helpers ───────────────────────────────────────────────────────────────────
function getS3Client(): S3Client {
  return new S3Client({
    region:      process.env.AWS_DEFAULT_REGION ?? 'us-east-1',
    credentials: {
      accessKeyId:     process.env.AWS_ACCESS_KEY_ID!,
      secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY!,
    },
  });
}

async function fetchS3Object(client: S3Client, bucket: string, key: string): Promise<string> {
  const cmd = new GetObjectCommand({ Bucket: bucket, Key: key });
  const res = await client.send(cmd);
  const chunks: Uint8Array[] = [];
  // @ts-expect-error — Body is a readable stream in Node.js
  for await (const chunk of res.Body) {
    chunks.push(chunk as Uint8Array);
  }
  return Buffer.concat(chunks).toString('utf-8');
}

function parsePositions(raw: string): Record<string, unknown>[] {
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed))       return parsed;
    if (Array.isArray(parsed?.positions)) return parsed.positions;
  } catch { /* fall through */ }
  return [];
}

function parseTrades(raw: string): Record<string, unknown>[] {
  return raw
    .split('\n')
    .map(l => l.trim())
    .filter(Boolean)
    .flatMap(line => {
      try { return [JSON.parse(line)]; }
      catch { return []; }
    });
}

// ── Phase 2 dataset metrics ───────────────────────────────────────────────────
/**
 * Filter trades.jsonl for Phase 2 ML dataset collection records.
 *
 * A valid Phase 2 entry must satisfy BOTH:
 *   version === "v2-bias-patch-stable"   (ensures only calibrated bot runs count)
 *   event   === "ENTRY"                  (only new position entries, not exits)
 *
 * Returns:
 *   phase2EntryCount — number of qualifying records (target: 300)
 *   phase2AvgEdge   — mean edge_entry across those records (decimal fraction,
 *                     e.g. 0.083 = 8.3%). NaN-safe; returns 0 if no data.
 */
function computePhase2Metrics(trades: Record<string, unknown>[]): {
  phase2EntryCount: number;
  phase2AvgEdge:    number;
} {
  const v2Entries = trades.filter(
    t => t.version === 'v2-bias-patch-stable' && t.event === 'ENTRY',
  );

  const edgeValues = v2Entries
    .map(t => parseFloat(String(t.edge_entry ?? t.edge ?? '')))
    .filter(isFinite);

  const avgEdge =
    edgeValues.length > 0
      ? edgeValues.reduce((s, v) => s + v, 0) / edgeValues.length
      : 0;

  return {
    phase2EntryCount: v2Entries.length,
    phase2AvgEdge:    +avgEdge.toFixed(6),
  };
}

/** Walk trades oldest→newest; last sigma_used per city wins. */
function latestSigma(trades: Record<string, unknown>[]): Record<string, number> {
  const sigma: Record<string, number> = {};
  for (const t of trades) {
    const city  = String(t.city  ?? '').toUpperCase().trim();
    const value = t.sigma_used;
    if (city && value !== undefined && value !== null) {
      const n = parseFloat(String(value));
      if (isFinite(n)) sigma[city] = n;
    }
  }
  return sigma;
}

/** Normalise one S3 position record → frontend Position schema. */
function mapPosition(p: Record<string, unknown>): Record<string, unknown> {
  const f = (v: unknown, def = 0): number => {
    const n = parseFloat(String(v ?? def));
    return isFinite(n) ? n : def;
  };

  const title       = String(p.question ?? p.market ?? p.title ?? 'Untitled').slice(0, 120);
  const id          = String(p.token_id  ?? p.asset_id ?? p.market_id ?? Math.random());
  const outcome     = String(p.outcome   ?? p.side ?? 'YES').toUpperCase();
  const size        = f(p.size);
  const entryPrice  = f(p.entry_price ?? p.avg_price);
  const currPrice   = f(p.current_price, entryPrice);
  const costBasis   = f(p.cost_basis ?? p.invested, entryPrice * size);
  const currValue   = f(p.current_value ?? p.value, currPrice * size);

  // Mark-to-Market unrealized PnL — signed, never abs()
  // YES: PnL = (curr − entry) × size  |  NO: identical arithmetic
  const cashPnl   = currValue - costBasis;
  const pctPnl    = costBasis > 0 ? (cashPnl / costBasis) * 100 : 0;

  const rawEdge   = p.edge_entry ?? p.edge ?? p.model_edge;
  const edgeEntry = rawEdge !== undefined ? f(rawEdge) : undefined;

  const mapped: Record<string, unknown> = {
    id,
    title,
    size,
    currentValue: +currValue.toFixed(6),
    cashPnl:      +cashPnl.toFixed(6),
    percentPnl:   +pctPnl.toFixed(4),
    entryPrice:   +entryPrice.toFixed(6),
    currentPrice: +currPrice.toFixed(6),
    outcome,
  };
  if (edgeEntry !== undefined) mapped.edgeEntry = +edgeEntry.toFixed(6);

  return mapped;
}

// ── Route handler ─────────────────────────────────────────────────────────────
export const dynamic    = 'force-dynamic'; // never cache — always fresh S3 data
export const revalidate = 0;              // disable ISR — force every request to hit S3

export async function GET(): Promise<NextResponse> {
  // ── Option A: proxy to the Python s3_api.py sidecar (if configured) ────────
  const proxyUrl = process.env.S3_API_URL;
  if (proxyUrl) {
    try {
      const res = await fetch(`${proxyUrl}/api/s3data`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`Proxy HTTP ${res.status}`);
      return NextResponse.json(await res.json());
    } catch (err) {
      console.error('[s3data] proxy error:', err);
      // fall through to direct AWS SDK path
    }
  }

  // ── Option B: direct AWS SDK call (default) ──────────────────────────────
  const bucket = process.env.S3_BUCKET;
  if (!bucket) {
    return NextResponse.json({
      s3Available:      false,
      positions:        [],
      sigmaLon:         2.2,
      sigmaSeo:         2.8,
      phase2EntryCount: 0,
      phase2AvgEdge:    0,
    });
  }

  const posKey    = process.env.S3_POSITIONS_KEY ?? 'positions.json';
  const tradesKey = process.env.S3_TRADES_KEY    ?? 'trades.jsonl';

  try {
    const client = getS3Client();

    // Fetch both objects in parallel
    const [positionsRaw, tradesRaw] = await Promise.all([
      fetchS3Object(client, bucket, posKey),
      fetchS3Object(client, bucket, tradesKey),
    ]);

    const rawPositions = parsePositions(positionsRaw);
    const positions    = rawPositions.map(mapPosition);
    const trades       = parseTrades(tradesRaw);
    const sigma        = latestSigma(trades);
    const phase2       = computePhase2Metrics(trades);

    console.log(
      `[s3data] OK — ${positions.length} positions | ` +
      `σLON=${sigma['LON'] ?? 2.2} σSEO=${sigma['SEO'] ?? 2.8} | ` +
      `Phase2 entries=${phase2.phase2EntryCount}/300 avgEdge=${(phase2.phase2AvgEdge * 100).toFixed(2)}%`,
    );

    return NextResponse.json({
      s3Available:      true,
      positions,
      sigmaLon:         sigma['LON'] ?? 2.2,
      sigmaSeo:         sigma['SEO'] ?? 2.8,
      phase2EntryCount: phase2.phase2EntryCount,
      phase2AvgEdge:    phase2.phase2AvgEdge,
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error('[s3data] AWS error:', msg);
    return NextResponse.json(
      {
        s3Available: false, positions: [], sigmaLon: 2.2, sigmaSeo: 2.8,
        phase2EntryCount: 0, phase2AvgEdge: 0, error: msg,
      },
      { status: 200 },
    );
  }
}
