# Build the defense slides. Requires MiKTeX (pdflatex on PATH).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

pdflatex -interaction=nonstopmode -enable-installer -halt-on-error slides.tex
pdflatex -interaction=nonstopmode -enable-installer -halt-on-error slides.tex

Write-Host "Built slides.pdf"
