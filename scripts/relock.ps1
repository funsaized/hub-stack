# Regenerate research-hub/requirements.lock.txt from requirements.txt (HUB-012).
#
# pip-compile runs inside a Linux python:3.12-slim container so environment
# markers resolve for the image platform (e.g. uvloop), not for Windows.
# The lock keeps the torch CPU extra index and hashes every distribution file,
# so the image build (`pip install --require-hashes`) is fully reproducible.
$ErrorActionPreference = "Stop"
$hub = (Resolve-Path (Join-Path $PSScriptRoot "..\research-hub")).Path
docker run --rm -v "${hub}:/work" -w /work python:3.12-slim sh -c "pip install -q pip-tools && pip-compile --generate-hashes --allow-unsafe --no-strip-extras --output-file requirements.lock.txt requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip-compile failed (exit $LASTEXITCODE)" }
Write-Output "requirements.lock.txt regenerated; rebuild images to apply."
