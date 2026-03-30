#!/usr/bin/env python3
"""Fix docstring and f-string label modifications from Phase 2."""

FILE = "/home/ubuntu/polymarket-temp-bot/tempbot_v427_stable_20260313.py"
BACKUP = FILE + ".bak.phase2"

with open(FILE, "r") as f:
    lines = f.readlines()
with open(BACKUP, "r") as f:
    orig_lines = f.readlines()

# We need to identify lines inside docstrings and revert them.
# Strategy: parse the file to find which lines are inside triple-quoted strings,
# then revert any changes on those lines.

# Also fix f-string labels that shouldn't have been changed:
# - EVENTS.kill_activated(f"daily_pnl=..." should stay as "daily_pnl=" not "STATE.daily_pnl="
# - log.info(f"STARTUP: _daily_reset_capital=..." should stay as label

def find_docstring_lines(file_lines):
    """Return set of 0-indexed line numbers that are inside triple-quoted strings."""
    in_docstring = False
    docstring_char = None
    docstring_lines = set()

    for i, line in enumerate(file_lines):
        stripped = line.strip()

        if not in_docstring:
            # Check if a triple-quoted string starts on this line
            for tq in ['"""', "'''"]:
                if tq in stripped:
                    # Count occurrences
                    count = stripped.count(tq)
                    if count == 1:
                        # Opens but doesn't close on same line
                        in_docstring = True
                        docstring_char = tq
                        docstring_lines.add(i)
                        break
                    elif count >= 2:
                        # Opens and closes on same line - it's a single-line docstring
                        # Don't mark as docstring content (the actual code might be on this line too)
                        pass
        else:
            docstring_lines.add(i)
            if docstring_char in stripped:
                in_docstring = False
                docstring_char = None

    return docstring_lines

# Find docstring lines in the ORIGINAL file
orig_docstring_lines = find_docstring_lines(orig_lines)

# Now we need to map original line numbers to new line numbers.
# The refactoring deleted 12 lines (global declarations). We need to figure out
# which new lines correspond to which original lines.

# Build a mapping: for each original line, find where it ended up in the new file.
# Since only deletions happened (no insertions), we can compute an offset.

# Actually, let's take a simpler approach: just find the specific lines in the
# refactored file that need reverting and fix them with exact replacements.

fixes = [
    # Docstring/changelog lines that were incorrectly modified
    # (original_text, wrong_text)
    (
        "                             Fix: equity_daily_loss_usd = capital - _daily_reset_capital (entrambi USD)",
        "                             Fix: equity_daily_loss_usd = capital - STATE._daily_reset_capital (entrambi USD)"
    ),
    (
        "  [v4.4-fix3] TOTAL_EXPOSURE si decrementa quando posizione risolve (intraday)",
        "  [v4.4-fix3] STATE.total_exposure si decrementa quando posizione risolve (intraday)"
    ),
    (
        "  [v4.1-C3] Global exposure cap: TOTAL_EXPOSURE <= 75% capital",
        "  [v4.1-C3] Global exposure cap: STATE.total_exposure <= 75% capital"
    ),
    (
        "    [v4.1-C3] Global exposure cap: TOTAL_EXPOSURE <= 75% capital.",
        "    [v4.1-C3] Global exposure cap: STATE.total_exposure <= 75% capital."
    ),
    (
        "    [v4.13-IL1] Rimosso 'global TOTAL_EXPOSURE': questa funzione non scrive mai",
        "    [v4.13-IL1] Rimosso 'global STATE.total_exposure': questa funzione non scrive mai"
    ),
    (
        "    TOTAL_EXPOSURE (vedi commento L1281). Il cap usa STATE.total_exposure.",
        "    STATE.total_exposure (vedi commento L1281). Il cap usa STATE.total_exposure."
    ),
    (
        "    finch\u00e9 non troviamo solo trade gi\u00e0 visti (_last_trade_ids). Cap memoria a",
        "    finch\u00e9 non troviamo solo trade gi\u00e0 visti (STATE._last_trade_ids). Cap memoria a"
    ),
    (
        '    """[v4.6-audit3] Sincronizza TOTAL_EXPOSURE = posizioni fillate + ordini pending.',
        '    """[v4.6-audit3] Sincronizza STATE.total_exposure = posizioni fillate + ordini pending.'
    ),
    (
        "    TOTAL_EXPOSURE = positions_exposure + pending_orders_exposure",
        "    STATE.total_exposure = positions_exposure + pending_orders_exposure"
    ),
    # f-string labels that should not have been changed
    (
        '        EVENTS.kill_activated(f"daily_pnl={effective_daily_pnl:.4f}", 24, capital, 0)',
        '        EVENTS.kill_activated(f"STATE.daily_pnl={effective_daily_pnl:.4f}", 24, capital, 0)'
    ),
    (
        '        log.info(f"STARTUP: _daily_reset_capital={real_cap:.2f}, kill_until reset")',
        '        log.info(f"STARTUP: STATE._daily_reset_capital={real_cap:.2f}, STATE.kill_until reset")'
    ),
]

fixed_count = 0
for i, line in enumerate(lines):
    for original, wrong in fixes:
        if line.rstrip() == wrong:
            lines[i] = original + "\n"
            fixed_count += 1
            break

with open(FILE, "w") as f:
    f.writelines(lines)

print(f"Fixed {fixed_count} incorrectly modified docstring/label lines.")
