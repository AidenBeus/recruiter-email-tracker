# Called by Windows Task Scheduler. It intentionally uses a small overlapping
# window; Gmail message IDs make reprocessing safe and prevent duplicates.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

& "C:\Users\aiden\miniconda3\python.exe" -m src.fetch_gmail --query "newer_than:1d"
exit $LASTEXITCODE
