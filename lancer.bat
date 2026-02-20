@echo off
title CSV Traitement
cd /d "%~dp0"

:: Vérifier que Python est installé
python --version >nul 2>&1
if errorlevel 1 (
    echo Python n'est pas installe ou pas dans le PATH.
    echo Telechargez-le sur https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Installer les dépendances si nécessaire
pip show chardet >nul 2>&1
if errorlevel 1 (
    echo Installation des dependances...
    pip install -r requirements.txt
    echo.
)

:: Lancer l'application
echo Lancement de CSV Traitement...
python main.py %*
