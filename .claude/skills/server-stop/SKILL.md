---
name: server-stop
description: Ferma il backend FastAPI e il frontend statico di PFolio-Manager avviati con la skill server-start. Trigger su richieste come "spegni il server", "ferma il dashboard", "kill l'API", "stop the server", "chiudi il portafoglio".
---

# Arresto server PFolio-Manager (API + frontend)

Termina i processi avviati dalla skill `server-start` (backend FastAPI sulla
porta 8000, frontend statico sulla porta 8080).

## Come funziona

1. Leggi `data/.server_pids.json`. Se non esiste, avvisa l'utente che non
   risultano server avviati da questa skill e chiedi se vuole comunque liberare
   le porte 8000/8080 (potrebbero essere occupate da processi avviati a mano) —
   in tal caso passa al punto 3 usando la ricerca per porta.
2. Per ciascun PID nel file, verifica che il processo sia ancora vivo e
   corrisponda a quanto atteso, poi terminalo:

   ```powershell
   Stop-Process -Id <pid> -Force -ErrorAction SilentlyContinue
   ```

   Se un PID non esiste più (processo già chiuso manualmente), ignoralo senza
   errore.
3. Fallback per porta (solo se il PID file manca o è incompleto): trova il
   processo in ascolto e terminalo, chiedendo conferma prima perché potrebbe
   non essere un processo di questo progetto:

   ```powershell
   Get-NetTCPConnection -LocalPort 8000,8080 -ErrorAction SilentlyContinue |
     Select-Object -ExpandProperty OwningProcess -Unique
   ```

4. Cancella `data/.server_pids.json` dopo aver fermato con successo i processi
   che referenziava.
5. Conferma all'utente quali processi sono stati fermati (API/frontend/entrambi)
   e segnala se qualche porta risultava già libera.

## Vincoli importanti

- Non usare mai `taskkill /F /IM python.exe` o simili che colpiscono tutti i
  processi Python: l'utente potrebbe avere altri script Python in esecuzione
  (es. Jupyter, altri progetti). Termina solo per PID specifico o, in fallback,
  per PID risolto dalla porta esatta 8000/8080.
- Se il fallback per porta trova un PID che non sembra plausibile per questo
  progetto (es. un IDE o un altro servizio web noto), chiedi conferma esplicita
  prima di terminarlo.
