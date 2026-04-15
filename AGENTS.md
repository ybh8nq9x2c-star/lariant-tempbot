# TempBot — Regole Agente

## REGOLA ASSOLUTA — BACKUP PRIMA DI TUTTO
Prima di modificare QUALSIASI file, SEMPRE:
1. Crea un branch backup con timestamp
2. Poi torna su dev e lavora lì

## WORKFLOW OBBLIGATORIO PER OGNI TASK

### 1. Pre-Modifica (Backup)
```bash
git checkout dev
git pull origin dev  # se possibile
git checkout -b backup/$(date +%Y%m%d_%H%M%S)
git add .
git commit -m "backup: pre-$(date +%Y%m%d_%H%M%S)"
git checkout dev
```

### 2. Sviluppo
- Lavora SOLO su branch `dev`
- Committa frequentemente con messaggi descrittivi
- Testa localmente prima di pushare

### 3. Commit & Push
```bash
git add <file_modificati>
git commit -m "feat/fix: descrizione chiara"
git push origin dev
```

### 4. Merge su main (solo dopo review)
```bash
git checkout main
git merge dev
git push origin main
```

## BRANCH STRUCTURE
- **main** → produzione EC2 (stabile, non toccare mai direttamente)
- **dev** → sviluppo attivo (qui lavora lagente)
- **backup/** → snapshot automatici pre-modifica (creati dallagente)

## FILE CRITICI — MANI LONTANE
| File | Perché |
|------|--------|
| `.env` | Contiene API keys e secrets |
| `*.pem` | Chiavi SSH private |
| `v5_book_data.jsonl` | Dataset di training (GB di dati) |
| `trades.jsonl` | Storico trade (non versionare) |

## DEPLOY SU EC2
- **NON è compito dellagente**
- Lutente fa manualmente: `git pull origin main` sul server EC2
- Lagente NON deve mai fare deploy o riavviare processi su EC2

## EMERGENZA — Rollback
Se qualcosa va storto:
```bash
git checkout backup/<timestamp>
git checkout -b fix/emergency-$(date +%Y%m%d)
```

## CONTATTO
Per dubbi sul workflow, consultare AGENTS.md prima di procedere.

