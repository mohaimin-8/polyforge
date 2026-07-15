# Build the thesis PDF. Requires MiKTeX (pdflatex + bibtex on PATH).
# Missing packages are auto-installed non-interactively.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

pdflatex -interaction=nonstopmode -enable-installer -halt-on-error main.tex
bibtex main
pdflatex -interaction=nonstopmode -enable-installer -halt-on-error main.tex
pdflatex -interaction=nonstopmode -enable-installer -halt-on-error main.tex

Write-Host "Built main.pdf"
