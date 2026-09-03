---
name: portfolio-update
description: Use this skill to update the PFolio-Manager investment portfolio dataset — either by ingesting new broker report files (Directa SIM, Fineco Bank, BNL Trading exports) dropped in raw_reports/, or by refreshing current market quotations when no new report files are present. Trigger on requests like "aggiorna il portafoglio", "elabora i nuovi report broker", "aggiorna le quotazioni", "update my portfolio", "process the broker exports", "refresh portfolio prices".
---

# Aggiornamento portafoglio (PFolio-Manager)

Questa skill orchestra l'ingestion dei report broker grezzi e l'aggiornamento del dataset
normalizzato del portafoglio. Tutta la logica vive nel package Python `pfolio_manager`
(non riscriverla qui dentro): questa skill si limita a eseguire il CLI e a riassumere
l'output all'utente.

## Come funziona

1. Verifica che `.venv` esista nella root del progetto. Se `requirements.txt` è stato
   modificato di recente o l'ambiente non risulta installato, esegui prima:
   `.venv/Scripts/python.exe -m pip install -r requirements.txt`
2. Esegui il CLI dalla root del repository:
   `.venv/Scripts/python.exe -m pfolio_manager.cli`
3. Il CLI internamente:
   - scansiona `raw_reports/` cercando file `.xlsx`/`.xls` non ancora elaborati (hash SHA-256
     confrontato con `ingested_sources` in `data/portfolio.json`);
   - se trova file nuovi: rileva il broker per struttura (non per nome file), esegue il
     parser corrispondente, converte i valori non-EUR, aggiorna gli holding (upsert) e
     aggiunge uno snapshot storico, poi sposta il file in `raw_reports/processed/<data>/`;
   - se non trova file nuovi: aggiorna solo le quotazioni di mercato correnti per tutti gli
     holding esistenti, tramite la fonte indicata per ciascun ISIN in
     `config/isin_ticker_map.json` (`yfinance` di default; `mot_bond` per obbligazioni
     quotate sul MOT di Borsa Italiana, letto per ISIN; `borsaitaliana_fund` per fondi con
     pagina dedicata su Borsa Italiana, letto per codice interno).
4. Stampa un riepilogo strutturato su stdout — leggilo e riassumilo all'utente (non
   limitarti a incollare l'output grezzo). Includi sempre:
   - numero di file processati e per quale broker/conto/data;
   - holding aggiunti vs aggiornati, e totale calcolato (EUR) vs totale dichiarato dal
     broker nel report — se c'è uno scostamento superiore a qualche euro, segnalalo
     esplicitamente (può indicare liquidità non censita come strumento, o un problema di
     conversione valuta);
   - lista di ISIN con quotazione `unmapped` (mai mappati su un ticker Yahoo Finance) o
     `lookup_failed` (ticker mappato ma non trovato/irraggiungibile) — questi vanno curati
     a mano in `config/isin_ticker_map.json`, non è previsto un fallback automatico;
   - eventuali fallback di cambio valuta (`stale_cache`/`unresolved`) o file non elaborati
     per un errore di formato.

## Vincoli importanti

- **Riservatezza dati**: `raw_reports/`, `data/portfolio.json` e `data/fx_cache.json` sono
  esclusi da git (vedi `.gitignore`). Non proporre mai di rimuovere queste esclusioni o di
  committare quei file/percorsi senza che l'utente lo chieda esplicitamente.
- **Non indovinare i ticker**: se un ISIN non è mappato, la skill deve segnalarlo
  all'utente e lasciare che sia lui a curare `config/isin_ticker_map.json` — non inventare
  un ticker plausibile.
- Questa skill copre solo l'ingestion e l'aggiornamento dati. Reportistica (HTML/PDF,
  grafici) e regole di ribilanciamento non fanno parte del suo scope.
