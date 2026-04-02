@echo off
REM Install PyMuPDF so PDF upload (POS grades) can render scanned PDFs.
REM Run from project folder. Uses venv if present.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo Using .venv (same as start.ps1).
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo Using venv.
)
echo Installing pymupdf...
pip install pymupdf
echo.
echo Verifying...
python -c "import fitz; print('PyMuPDF OK - restart your Django server and try the PDF upload again.')"
pause
