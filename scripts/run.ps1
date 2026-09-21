# Start DigiKitab. Replaces `npm start` (OI-5 retired the Express layer).
#
# One process now, not two: the FastAPI app serves the frontend from its own
# origin, so there is no static host to run alongside it and no second port.
#
# The interpreter choice is the part that matters and is why this is a script
# rather than a documented command line. sentence-transformers lives in the
# project venv and not in the shared global Python (F-24), and launching from
# the wrong one does not crash — `search_books` filters chunks to the query's
# own vector space, so a MiniLM corpus queried by an LSA encoder returns
# nothing for every query, quietly (OI-9). Choosing it here is what stops
# that depending on who remembers.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$venv = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venv) {
    $python = $venv
    $which = '.venv'
} else {
    $python = 'python'
    $which = 'system python (no .venv found - semantic search will be unavailable)'
}

$bindHost = if ($env:HOST) { $env:HOST } else { '127.0.0.1' }
$port = if ($env:PORT) { $env:PORT } else { '8000' }

Write-Host "DigiKitab - $which"
Write-Host "  http://$bindHost`:$port"
Write-Host ''

# Loopback by default. Binding every interface is a deliberate act, not the
# default someone inherits (OI-5); set HOST to widen it on purpose.
Push-Location (Join-Path $root 'backend')
try {
    & $python -m uvicorn main:app --host $bindHost --port $port @args
} finally {
    Pop-Location
}
