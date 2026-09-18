---
name: server-start
description: Avvia il backend FastAPI (pfolio_manager.api) e il frontend statico di PFolio-Manager in background, così il dashboard è raggiungibile su localhost. Trigger su richieste come "avvia il server", "apri il dashboard", "fai partire l'API", "start the server", "run the dashboard", "avvia il portafoglio".
---

# Avvio server PFolio-Manager (API + frontend)

Questa skill avvia in background i due processi necessari per usare il dashboard:
il backend FastAPI (porta 8000) e il server statico del frontend (porta 8080).
Non tocca il database né i dati — usa `portfolio-update` per quello.

## Come funziona

1. Verifica se i server risultano già attivi leggendo `data/.server_pids.json`
   (se esiste) e controllando che i PID lì salvati siano ancora vivi
   (`Get-Process -Id <pid> -ErrorAction SilentlyContinue`). Se entrambi sono già
   in esecuzione, dillo all'utente e non avviarne di nuovi.
2. Per ogni processo non attivo, avvialo in background dalla root del repo con
   `Start-Process -PassThru` (necessario per catturare il PID; non usare `&&`/`;`
   in un'unica shell bloccante):

   ```powershell
   # API — dalla root del repo
   $api = Start-Process -FilePath ".venv\Scripts\python.exe" `
     -ArgumentList "-m","uvicorn","pfolio_manager.api:app","--host","0.0.0.0","--port","8000" `
     -WorkingDirectory "." -PassThru -WindowStyle Hidden

   # Frontend — working directory frontend/
   $front = Start-Process -FilePath "python" `
     -ArgumentList "-m","http.server","8080" `
     -WorkingDirectory "frontend" -PassThru -WindowStyle Hidden
   ```

3. **Non fidarti del PID restituito da `Start-Process`**: per `python`/l'entry
   point di `.venv`, quel PID è spesso un processo "launcher" che spawna
   l'interprete vero e poi termina — il PID salvato risulterebbe già morto un
   attimo dopo, rendendo `server-stop` inutile. Risolvi invece il PID reale in
   ascolto su ciascuna porta, con un piccolo retry perché il bind non è istantaneo:

   ```powershell
   function Resolve-ListenerPid($port, $fallbackId) {
     for ($i = 0; $i -lt 10; $i++) {
       $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
         Select-Object -First 1
       if ($conn) { return $conn.OwningProcess }
       Start-Sleep -Milliseconds 500
     }
     return $fallbackId  # ripiego se la risoluzione per porta fallisce
   }

   $apiPid = Resolve-ListenerPid -port 8000 -fallbackId $api.Id
   $frontPid = Resolve-ListenerPid -port 8080 -fallbackId $front.Id
   ```

4. Salva i PID **risolti per porta** (non quelli grezzi di `Start-Process`) in
   `data/.server_pids.json` (crea/sovrascrive), ad es.:
   `{"api_pid": 1234, "frontend_pid": 5678, "started_at": "<ISO timestamp>"}`.
   Questo file è lo stato che la skill `server-stop` userà per terminarli in modo
   pulito.
5. Verifica che entrambe le porte rispondano
   (`Invoke-WebRequest http://localhost:8000/docs`, `http://localhost:8080`).
   Se uno dei due non risponde, controlla se `.venv` esiste ed è installato
   (vedi requirements.txt) prima di segnalare un errore generico.
6. Riporta all'utente gli URL: `http://localhost:8000` (API, docs su `/docs`) e
   `http://localhost:8080` (dashboard).

## Vincoli importanti

- Non usare `--reload` su uvicorn: su Windows in questo repo risulta inaffidabile
  (rileva la modifica ma non riavvia davvero) — se il codice dell'API cambia va
  fermato e riavviato manualmente (vedi skill `server-stop` + questa).
- Non avviare un secondo backend/frontend se uno risulta già in esecuzione e
  sano: chiedi prima se l'utente vuole riavviarlo (in tal caso usa prima
  `server-stop`).
- Questa skill non modifica dati o configurazione: si limita a far partire i
  processi.
