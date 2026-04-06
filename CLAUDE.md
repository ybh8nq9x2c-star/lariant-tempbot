# CLAUDE.md — TempBot Working Rules

## Missione
TempBot è un market-maker/arbitrageur su mercati meteo (Polymarket).
Opera in produzione su EC2 (eu-west-1, Ireland).
Obiettivo attuale: aggiungere un layer ML (LightGBM) per migliorare la stima
dell'edge senza rompere il bot live.

---

## Path critici

| Path | Ruolo |
|------|-------|
| `tempbot_v427_stable_20260313.py` (root) | Bot master locale v4.31 / sorgente patch; path live EC2 da verificare |
| `polymarket-temp-bot/` | Snapshot EC2 (Mar 18) — leggermente vecchio |
| `polymarket-temp-bot/config.json` | Parametri live (hot-reload ogni 60s) |
| `polymarket-temp-bot/config_override.json` | Override runtime manuale |
| `polymarket-temp-bot/trades.jsonl` | Fills storici — fonte primaria training ML |
| `trades.jsonl` (root) | Versione più aggiornata (443KB) — usare questa per ML |
| `polymarket-temp-bot/edge_cache.json` | Edge per token, scritto dal bot live |
| `polymarket-temp-bot/forecast_weights.json` | Pesi ensemble meteo auto-tuned |
| `tempbot-ireland.pem` | Chiave SSH EC2 — non toccare mai |

---

## Regole di sicurezza assolute

1. **Mai modificare il bot live senza conferma esplicita dell'utente.**
   Questo include `tempbot_v427_stable_20260313.py` ovunque si trovi.
2. **`trades.jsonl` è read-only.** Nessuna scrittura, nessun troncamento.
3. **Nessun refactor strutturale** senza piano approvato e test.
4. **`phase2_refactor.py` non va eseguito** fino a test completo su copia isolata.
5. **Nessun deploy, restart, push su EC2** senza ordine esplicito.
6. **Nessun comando distruttivo** (rm, git reset --hard, truncate, ecc.).
7. **La chiave .pem può essere usata nel terminale per connessioni ssh/scp verso l'EC2 solo sotto esplicita autorizzazione, ma il suo contenuto in chiaro non deve MAI essere letto (tramite cat/read), loggato o esposto nel contesto della chat.**
8. **Se una regola vale solo per task ML specifici, spostarla in skill o file dedicato, non in CLAUDE.md.**

---

## Regole di modifica: patch chirurgiche only

- Ogni modifica al bot viene prima scritta come patch script (stile `patch_tempbot_phases.py`).
- La patch contiene: anchor preciso, old_string, new_string, label.
- Nessuna sostituzione glob su file di produzione.
- Prima si mostra la bozza della patch, poi si attende conferma, poi si applica.
- Le patch vanno applicate su una copia locale prima di qualsiasi sync EC2.

---

## Workflow corretto

```
1. AUDIT     → leggi i file rilevanti, mappa lo stato attuale
2. PIANO     → proponi approccio, mostra bozza
3. CONFERMA  → attendi ok esplicito dell'utente
4. PATCH     → applica chirurgicamente
5. VERIFICA  → controlla output, log, nessun side effect
```
Non saltare passi. Non anticipare la patch senza conferma.

---

## Separazione netta: produzione / dati / ML

| Layer | Cosa contiene | Regola |
|-------|--------------|--------|
| **Produzione** | `tempbot_v427_stable_20260313.py`, `config.json`, `edge_cache.json` | Solo patch confermate |
| **Dati** | `trades.jsonl`, `events.jsonl`, `monitor_state.json` | Read-only |
| **ML** | `ml/`, `data/` (da creare) | Sviluppo libero, mai importato live senza conferma |

Il layer ML deve essere **opzionale e con fallback graceful**: se il modello non
esiste o crasha, il bot continua con la logica attuale. Zero impatto su live.

---

## Formato risposta atteso di Claude

- Bozze prima, salvataggio dopo conferma.
- Patch mostrate prima di essere applicate.
- Nessuna modifica silenziosa a file di produzione.
- Se qualcosa è ambiguo, chiedere prima di procedere.
- Risposte concise. Nessun recap inutile di ciò che è già visibile.
