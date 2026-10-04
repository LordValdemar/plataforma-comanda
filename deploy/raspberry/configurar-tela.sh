#!/usr/bin/env bash
# Transforma um Raspberry Pi (ou qualquer Linux com tela) no player de uma TV:
# ao ligar, abre o endereço da tela em tela cheia, sem apagar a tela e com som.
#
# Rode com o usuário que entra na área de trabalho (NÃO use sudo):
#   ./deploy/raspberry/configurar-tela.sh http://192.168.0.10:5000/tela
#
# Para desfazer:  ./deploy/raspberry/configurar-tela.sh --remover
set -euo pipefail

INICIADOR="$HOME/.local/bin/painel-tela.sh"
AUTOSTART_XDG="$HOME/.config/autostart/painel-tela.desktop"
AUTOSTART_LABWC="$HOME/.config/labwc/autostart"
MARCA="# painel-propagandas"

if [ "$(id -u)" -eq 0 ]; then
  echo "Não use sudo: rode com o usuário que entra na área de trabalho do Raspberry Pi." >&2
  exit 1
fi

if [ "${1:-}" = "--remover" ]; then
  rm -f "$INICIADOR" "$AUTOSTART_XDG"
  [ -f "$AUTOSTART_LABWC" ] && sed -i "/$MARCA/d" "$AUTOSTART_LABWC"
  echo "Removido. A TV não abre mais o painel sozinha ao ligar."
  exit 0
fi

ENDERECO="${1:-}"
if [[ ! "$ENDERECO" =~ ^https?://[^/]+/(tela(/[A-Za-z0-9_-]+)?|player)$ ]]; then
  echo "Uso: $0 ENDERECO-DA-TELA"
  echo "Exemplo: $0 http://192.168.0.10:5000/tela"
  echo "O endereço de cada tela aparece no painel, em \"Telas\"."
  exit 1
fi
SERVIDOR="$(echo "$ENDERECO" | sed -E 's#^(https?://[^/]+)/.*#\1#')"

if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null; then
  echo "Atenção: o navegador Chromium não está instalado. Instale com:"
  echo "  sudo apt install -y chromium || sudo apt install -y chromium-browser"
fi

# ---------------------------------------------------------------------------
# Iniciador: espera o servidor responder antes de abrir o navegador.
# Sem isso, se a TV ligar antes do servidor, o Chromium fica preso numa página
# de erro. Depois que o player abre, ele mesmo lida com quedas de rede.
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$INICIADOR")"
cat > "$INICIADOR" <<INICIADOR
#!/usr/bin/env bash
# Gerado por deploy/raspberry/configurar-tela.sh
ENDERECO="$ENDERECO"
SERVIDOR="$SERVIDOR"

# Já está aberto? (o autostart pode ser chamado por mais de um mecanismo)
pgrep -f "chromium.*--kiosk" >/dev/null && exit 0

until curl -fs --max-time 5 "\$SERVIDOR/saude" >/dev/null; do
  sleep 5
done

NAVEGADOR="\$(command -v chromium || command -v chromium-browser)"
PREFERENCIAS="\$HOME/.config/chromium/Default/Preferences"
# Se a TV foi desligada da tomada, evita a barra "Restaurar páginas?".
[ -f "\$PREFERENCIAS" ] && sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "\$PREFERENCIAS"

# Sem --incognito: o player guarda no navegador as exibições feitas sem rede,
# para enviar ao relatório quando a conexão voltar.
exec "\$NAVEGADOR" \\
  --kiosk "\$ENDERECO" \\
  --noerrdialogs --disable-infobars --no-first-run \\
  --autoplay-policy=no-user-gesture-required \\
  --check-for-update-interval=31536000 \\
  --password-store=basic
INICIADOR
chmod +x "$INICIADOR"

# ---------------------------------------------------------------------------
# Abrir ao entrar na área de trabalho (Raspberry Pi OS com X11/LXDE ou Wayland/labwc).
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$AUTOSTART_XDG")"
cat > "$AUTOSTART_XDG" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Painel de Propagandas - Tela
Exec=$INICIADOR
X-GNOME-Autostart-enabled=true
DESKTOP

mkdir -p "$(dirname "$AUTOSTART_LABWC")"
touch "$AUTOSTART_LABWC"
sed -i "/$MARCA/d" "$AUTOSTART_LABWC"
echo "$INICIADOR & $MARCA" >> "$AUTOSTART_LABWC"

# ---------------------------------------------------------------------------
# Não deixar a tela apagar (descanso de tela).
# ---------------------------------------------------------------------------
if command -v raspi-config >/dev/null; then
  if sudo -n true 2>/dev/null || [ -t 0 ]; then
    sudo raspi-config nonint do_blanking 1 && echo "Descanso de tela desativado."
  fi
else
  echo "Desative o descanso de tela nas configurações de energia/tela do sistema."
fi

cat <<FIM

Pronto! Ao ligar, esta TV abre sozinha:
  $ENDERECO

Dicas:
  - No Raspberry Pi, deixe o login automático na área de trabalho ligado
    (sudo raspi-config > System Options > Auto Login; em versões antigas,
    Boot / Auto Login > Desktop Autologin).
  - Para testar agora sem reiniciar:  $INICIADOR &
  - Para sair da tela cheia: Alt+F4.
  - Para trocar de tela, rode este script de novo com o endereço novo.
FIM
