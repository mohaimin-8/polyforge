# Build the journal manuscript (MiKTeX). Produces main.pdf (review copy by
# default; set \blindfalse in main.tex for the author copy).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python make_tables.py
pdflatex -interaction=nonstopmode -enable-installer -halt-on-error main.tex
pdflatex -interaction=nonstopmode -enable-installer -halt-on-error main.tex
pdflatex -interaction=nonstopmode -enable-installer -halt-on-error main.tex
Write-Host "Built main.pdf"
