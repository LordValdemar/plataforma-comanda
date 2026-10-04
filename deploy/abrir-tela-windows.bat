@echo off
REM Abre uma tela do Painel de Propagandas em tela cheia no Windows (Microsoft Edge).
REM 1. Troque o IP do ENDERECO abaixo pelo do servidor. Na primeira vez, conecte
REM    a TV lendo o QR code com o celular; depois ela abre direto nas propagandas.
REM 2. Para abrir sozinho ao ligar: Win+R, digite shell:startup e coloque
REM    um atalho para este arquivo na pasta que abrir.
REM Para sair da tela cheia: Alt+F4.

set ENDERECO=http://192.168.0.10:5000/tela

REM Espera o servidor responder antes de abrir (se o PC ligar antes do servidor).
for /f "tokens=1-3 delims=/" %%a in ("%ENDERECO%") do set SERVIDOR=%%a//%%b
:esperar
curl -fs --max-time 5 "%SERVIDOR%/saude" >nul 2>&1
if errorlevel 1 (
    echo Aguardando o servidor %SERVIDOR% ...
    timeout /t 5 /nobreak >nul
    goto esperar
)

start "" msedge --kiosk "%ENDERECO%" --edge-kiosk-type=fullscreen --no-first-run --autoplay-policy=no-user-gesture-required
