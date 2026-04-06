/**
 * app/api/predict/route.ts — LariantAI ML Predict Route
 * =======================================================
 * Chiama predict_single.py (LightGBM) via child_process.
 *
 * POST /api/predict
 * Body: { size_contracts, edge_entry, entry_price, city, Tdays, edge_abs? }
 * Response: { prob_win, expected_edge, signal }
 *
 * Env vars:
 *   ML_PYTHON_BIN   path to python3 (default: python3)
 *   ML_SCRIPT_PATH  path to predict_single.py
 *                   (default: /Users/matteo/Desktop/predict_single.py)
 */

import { NextRequest, NextResponse } from 'next/server';
import { spawn }                     from 'child_process';
import path                          from 'path';

const PYTHON_BIN  = process.env.ML_PYTHON_BIN  ?? 'python3';
const SCRIPT_PATH = process.env.ML_SCRIPT_PATH ??
  path.join(process.cwd(), '..', 'predict_single.py');

interface PredictInput {
  size_contracts: number;
  edge_entry:     number;
  entry_price:    number;
  city:           'london' | 'seoul' | string;
  Tdays?:         number;
  edge_abs?:      number;
}

interface PredictOutput {
  prob_win:       number;
  expected_edge:  number;
  signal:         'BUY' | 'SKIP';
}

function runPython(input: PredictInput): Promise<PredictOutput> {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(input);
    const proc    = spawn(PYTHON_BIN, [SCRIPT_PATH, payload]);

    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.stderr.on('data', (d: Buffer) => { stderr += d.toString(); });

    proc.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(`predict_single.py exited ${code}: ${stderr.trim()}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout.trim()) as PredictOutput);
      } catch {
        reject(new Error(`Invalid JSON from predict_single.py: ${stdout.trim()}`));
      }
    });

    proc.on('error', (err) => reject(err));

    // Timeout 10s
    setTimeout(() => {
      proc.kill();
      reject(new Error('predict_single.py timeout after 10s'));
    }, 10_000);
  });
}

export async function POST(req: NextRequest) {
  let body: PredictInput;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const required: (keyof PredictInput)[] = ['size_contracts', 'edge_entry', 'entry_price', 'city'];
  for (const field of required) {
    if (body[field] === undefined || body[field] === null) {
      return NextResponse.json({ error: `Missing field: ${field}` }, { status: 422 });
    }
  }

  try {
    const result = await runPython(body);
    return NextResponse.json(result);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
