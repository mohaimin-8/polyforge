# Build the FGCS manuscript. Requires MiKTeX (pdflatex + bibtex on PATH).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
pdflatex -interaction=nonstopmode -enable-installer -halt-on-error main.tex
bibtex main
pdflatex -interaction=nonstopmode -enable-installer -halt-on-error main.tex
pdflatex -interaction=nonstopmode -enable-installer -halt-on-error main.tex
Write-Host "Built main.pdf"
