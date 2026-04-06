# PHASE_STATE.md — TempBot Roadmap Tracker

_Aggiornato: 2026-04-04_

---

## Fase corrente: FASE 2 — Bias / Raccolta (in chiusura)

**Stato:** IN PREPARAZIONE (struttura non ancora creata)

Transizione in preparazione verso FASE 3 — Regimes & Machine Learning (LightGBM)

---

## Obiettivo corrente

Preparare dataset, target, feature schema e struttura LightGBM offline senza
modificare il bot live e senza fissare ancora il target finale.

---

## Fase 2 completata — condizione di uscita

Fase 2 completata = counter 300+ con tag v2-bias-patch-stable

---

## Stato LightGBM

| Item | Stato |
|------|-------|
| Dipendenza installata nel venv | NON VERIFICATO |
| Feature schema definito | NO |
| Dataset labels (resolved outcomes) | NO — da estrarre da trades.jsonl |
| Primo modello addestrato | NO |
| Integrazione bot (opzionale) | NO |
| Test offline | NO |

---

## Checklist ingresso Fase 3

- [ ] `ml/features.py` genera feature pulite da `trades.jsonl`
- [ ] `ml/trainer.py` allena e serializza un modello LightGBM riproducibile
- [ ] `ml/predictor.py` espone `predict_edge(features) -> float` con fallback
- [ ] Il bot importa `predictor` in modo opzionale (try/except, no crash se assente)
- [ ] Backtest su dati storici mostra lift vs baseline (edge medio attuale)
- [ ] Nessun parametro live è stato modificato durante il processo

---

## Cosa è permesso fare oggi

- Leggere `trades.jsonl` e `events.jsonl` per capire la struttura dei dati
- Creare `ml/` e `data/` come directory di lavoro
- Scrivere `ml/features.py` in isolamento
- Verificare che lightgbm sia installabile nel venv (`pip install lightgbm --dry-run`)
- Creare `CLAUDE.md` e `PHASE_STATE.md`

---

## Cosa è vietato fare oggi

- Modificare `tempbot_v427_stable_20260313.py`
- Modificare `config.json` o `config_override.json`
- Scrivere o troncare `trades.jsonl`
- Eseguire `phase2_refactor.py`
- Fare deploy o restart su EC2
- Aggiungere import al bot senza piano approvato
- Eseguire comandi distruttivi (rm, reset, truncate)

---

## Priorità operative immediate

1. Verificare installabilità di `lightgbm` nel venv (Python 3.14 — potenziale problema)
2. Analizzare struttura `trades.jsonl` per definire feature schema
3. Verificare quanti trade hanno outcome resolved (label disponibile)
4. Creare `ml/features.py` — solo feature engineering, nessun training ancora
5. Aggiornare questo file a ogni milestone

---

## Rischi principali da non dimenticare

| Rischio | Dettaglio |
|---------|-----------|
| Python 3.14 + LightGBM | LightGBM potrebbe non avere wheels per Python 3.14 — verificare prima |
| Labels scarsi | 611 fills nel snapshot Mar 18, non tutti resolved — dataset ML potrebbe essere piccolo |
| Due copie bot | Root (v4.31, 4665 righe) vs subfolder (4578 righe) — chiarire quale è su EC2 |
| `phase2_refactor.py` non applicato | Il refactor global→STATE esiste ma non è nel bot master — non applicare senza test |
| Fallback obbligatorio | Il predictor ML deve sempre avere try/except — mai bloccare il loop principale |
