$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --reload"

Push-Location frontend
if (-not (Test-Path "node_modules")) {
    npm ci
}
npm start
Pop-Location

