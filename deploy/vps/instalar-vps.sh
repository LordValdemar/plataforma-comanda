#!/usr/bin/env bash
# Instala o Painel de Propagandas numa VPS (Ubuntu 22.04/24.04 ou Debian 12)
# com HTTPS automático, firewall e atualizações de segurança.
#
# Uso (veja docs/HOSPEDAGEM.md):
#   sudo git clone <repositório> /opt/painel-propagandas
#   sudo /opt/painel-propagandas/deploy/vps/instalar-vps.sh painel.sualoja.com.br voce@sualoja.com.br
#
# Para testar numa máquina virtual, sem domínio público (docs/TESTE-MAQUINA-VIRTUAL.md):
#   sudo ./deploy/vps/instalar-vps.sh painel.teste voce@exemplo.com --teste
#
# Pode rodar de novo sem problema: o que já está configurado é mantido
# (inclusive /etc/painel-propagandas/ambiente, com as suas chaves).
set -euo pipefail

DOMINIO="${1:-}"
EMAIL="${2:-}"
MODO_TESTE=0
[ "${3:-}" = "--teste" ] && MODO_TESTE=1
PASTA="$(cd "$(dirname "$0")/../.." && pwd)"
USUARIO="painel"
PASTA_CONFIG="/etc/painel-propagandas"
AMBIENTE="$PASTA_CONFIG/ambiente"

passo() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33mAtenção:\033[0m %s\n' "$*"; }
erro() { printf '\033[1;31mErro:\033[0m %s\n' "$*" >&2; exit 1; }

if [ -z "$DOMINIO" ] || [ -z "$EMAIL" ]; then
  echo "Uso: sudo $0 DOMINIO EMAIL"
  echo "Exemplo: sudo $0 painel.sualoja.com.br voce@sualoja.com.br"
  exit 1
fi
[ "$(id -u)" -eq 0 ] || erro "rode com sudo: sudo $0 $DOMINIO $EMAIL"
[[ "$DOMINIO" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || erro "domínio inválido: $DOMINIO"
[[ "$EMAIL" == *@*.* ]] || erro "e-mail inválido: $EMAIL"
[ -f "$PASTA/servidor.py" ] || erro "não encontrei o programa em $PASTA"
command -v apt-get >/dev/null || erro "este instalador é para Ubuntu ou Debian"

# ---------------------------------------------------------------------------
passo "Instalando os pacotes do sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv git curl ufw fail2ban unattended-upgrades
# Caddy sempre do repositório oficial: o do Ubuntu/Debian é antigo (2.6) e aceita
# assinaturas SHA-1 no TLS 1.2, sem criptografia pós-quântica. Rodar o instalador de
# novo atualiza o Caddy para a versão mais nova.
if [ ! -f /etc/apt/sources.list.d/caddy-stable.list ]; then
  apt-get install -y -q debian-keyring debian-archive-keyring apt-transport-https gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
fi
CADDY_ANTES="$(caddy version 2>/dev/null | cut -d' ' -f1 || true)"
apt-get install -y -q caddy
CADDY_DEPOIS="$(caddy version | cut -d' ' -f1)"
echo "Caddy: $CADDY_DEPOIS"

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || erro "é preciso Python 3.10 ou mais novo (use Ubuntu 22.04+ ou Debian 12+)"

# ---------------------------------------------------------------------------
passo "Conferindo o domínio $DOMINIO"
if [ "$MODO_TESTE" -eq 1 ]; then
  echo "Modo de teste: certificado HTTPS de teste (o navegador vai mostrar um aviso). Não use em produção."
else
IP_SERVIDOR="$(curl -4 -fs --max-time 10 https://api.ipify.org || true)"
IP_DOMINIO="$(getent ahostsv4 "$DOMINIO" | awk 'NR==1 {print $1}' || true)"
if [ -z "$IP_DOMINIO" ]; then
  aviso "$DOMINIO ainda não aponta para nenhum IP. Crie o registro DNS do tipo A apontando para ${IP_SERVIDOR:-o IP deste servidor}."
  aviso "A instalação continua; o HTTPS passa a funcionar sozinho alguns minutos depois que o DNS propagar."
elif [ -n "$IP_SERVIDOR" ] && [ "$IP_DOMINIO" != "$IP_SERVIDOR" ]; then
  aviso "$DOMINIO aponta para $IP_DOMINIO, mas este servidor é $IP_SERVIDOR. Corrija o DNS para o HTTPS funcionar."
else
  echo "OK: $DOMINIO aponta para este servidor ($IP_DOMINIO)."
fi
fi

# ---------------------------------------------------------------------------
passo "Criando o usuário do sistema \"$USUARIO\" (sem login)"
if ! id -u "$USUARIO" >/dev/null 2>&1; then
  useradd --system --home-dir "$PASTA" --no-create-home --shell /usr/sbin/nologin "$USUARIO"
fi

# O código pertence ao root (o serviço não consegue alterá-lo); só os dados são do serviço.
chown -R root:root "$PASTA"
chmod -R go-w "$PASTA"
mkdir -p "$PASTA/dados"
chown -R "$USUARIO:$USUARIO" "$PASTA/dados"
chmod 750 "$PASTA/dados"

# ---------------------------------------------------------------------------
passo "Instalando as dependências do Python"
# Um ambiente criado pela metade (sem o pip) é apagado e criado de novo.
if [ ! -x "$PASTA/.venv/bin/pip" ]; then
  rm -rf "$PASTA/.venv"
  python3 -m venv "$PASTA/.venv"
fi
"$PASTA/.venv/bin/pip" install --quiet --upgrade pip
"$PASTA/.venv/bin/pip" install --quiet -r "$PASTA/requirements.txt"

# ---------------------------------------------------------------------------
passo "Configuração em $AMBIENTE"
mkdir -p "$PASTA_CONFIG"
if [ ! -f "$AMBIENTE" ]; then
  TOKEN_WEBHOOK="$(python3 -c 'import secrets; print(secrets.token_urlsafe(40))')"
  cat > "$AMBIENTE" <<CONFIG
# Configuração do Painel de Propagandas. Depois de editar, reinicie:
#   sudo systemctl restart painel-propagandas
# Valores com espaços ou com "\$" vão entre aspas SIMPLES. Ex.: ASAAS_API_KEY='\$aact_prod_...'

# --- Servidor (não altere: o painel só escuta localmente, atrás do Caddy) ---
HOST=127.0.0.1
PORTA=5000
ATRAS_DE_PROXY=1
COOKIE_SEGURO=1
PASTA_DADOS=$PASTA/dados

# --- Sua marca ---
NOME_PLATAFORMA='Painel de Propagandas'
CONTATO_PLATAFORMA=$EMAIL
FUSO_HORARIO=America/Sao_Paulo

# --- Plataforma de assinatura (Painel + Comanda) ---
# 1 = a página inicial mostra os planos e qualquer pessoa cria a conta da loja e assina pelo site.
CADASTRO_ABERTO=0
# Dias grátis até a primeira fatura de quem assina pelo site (0 = cobra já no primeiro dia).
TESTE_GRATIS_DIAS=7

# --- Cobrança automática (Asaas) ---
# Cadastre no Asaas o webhook https://$DOMINIO/webhooks/asaas com o token abaixo.
ASAAS_API_KEY=
ASAAS_AMBIENTE=sandbox
ASAAS_WEBHOOK_TOKEN=$TOKEN_WEBHOOK
COBRANCA_TOLERANCIA_DIAS=5

# --- E-mail para alertas (opcional) ---
SMTP_HOST=
SMTP_PORTA=587
SMTP_USUARIO=
SMTP_SENHA=
SMTP_REMETENTE=
CONFIG
  echo "Criado. O token do webhook do Asaas foi gerado automaticamente."
else
  echo "Já existe: mantido como está."
fi
# Contém chaves secretas: só o root edita e só o serviço lê.
chown root:"$USUARIO" "$AMBIENTE"
chmod 640 "$AMBIENTE"

# ---------------------------------------------------------------------------
passo "Instalando o serviço"
sed "s#__PASTA__#$PASTA#g" "$PASTA/deploy/vps/painel-propagandas.service" \
  > /etc/systemd/system/painel-propagandas.service
systemctl daemon-reload
systemctl enable painel-propagandas >/dev/null
systemctl restart painel-propagandas

# ---------------------------------------------------------------------------
passo "Configurando o HTTPS (Caddy) para $DOMINIO"
if [ -f /etc/caddy/Caddyfile ] && ! grep -q "Gerado por deploy/vps/instalar-vps.sh" /etc/caddy/Caddyfile; then
  cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.antes-do-painel.$(date +%Y%m%d%H%M%S)"
fi
sed -e "s#__DOMINIO__#$DOMINIO#g" -e "s#__EMAIL__#$EMAIL#g" \
  "$PASTA/deploy/vps/Caddyfile.modelo" > /etc/caddy/Caddyfile
if [ "$MODO_TESTE" -eq 1 ]; then
  # Certificado emitido por uma autoridade local do próprio Caddy, sem precisar de domínio público.
  sed -i "s#^\tencode gzip#\ttls internal\n\tencode gzip#" /etc/caddy/Caddyfile
fi
# www.dominio leva para o endereço principal, se o DNS do www já existir (senão o Caddy
# ficaria tentando, sem sucesso, tirar o certificado de um nome que não aponta para cá).
if [ "$MODO_TESTE" -eq 0 ] && [[ "$DOMINIO" != www.* ]] && getent ahostsv4 "www.$DOMINIO" >/dev/null; then
  printf '\n# Gerado por deploy/vps/instalar-vps.sh: www leva para o endereço principal.\nwww.%s {\n\tredir https://%s{uri} permanent\n}\n' \
    "$DOMINIO" "$DOMINIO" >> /etc/caddy/Caddyfile
  echo "OK: www.$DOMINIO vai levar para https://$DOMINIO"
elif [ "$MODO_TESTE" -eq 0 ] && [[ "$DOMINIO" != www.* ]]; then
  echo "Dica: crie no DNS o registro www (CNAME para $DOMINIO) e rode este instalador de novo para o www também funcionar."
fi
if ! SAIDA_CADDY="$(caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1)"; then
  echo "$SAIDA_CADDY" >&2
  erro "a configuração do Caddy é inválida (veja acima)"
fi
systemctl enable caddy >/dev/null
if [ "$CADDY_ANTES" != "$CADDY_DEPOIS" ]; then
  systemctl restart caddy   # versão nova do Caddy: só vale depois de reiniciar
else
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
fi

# ---------------------------------------------------------------------------
passo "Firewall: liberando só SSH, HTTP e HTTPS"
# Descobre a porta do SSH para não trancar você fora do servidor (padrão: 22).
PORTA_SSH="$( (sshd -T 2>/dev/null || true) | awk '$1 == "port" {print $2; exit}')"
ufw allow "${PORTA_SSH:-22}/tcp" >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
echo "Portas abertas: ${PORTA_SSH:-22} (SSH), 80 e 443."

passo "Ativando as atualizações automáticas de segurança e o fail2ban"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT
systemctl enable --now fail2ban >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
passo "Conferindo se está tudo funcionando"
for _ in $(seq 1 30); do
  curl -fsS --noproxy "*" --max-time 2 http://127.0.0.1:5000/saude >/dev/null 2>&1 && break
  sleep 1
done
if curl -fsS --noproxy "*" --max-time 2 http://127.0.0.1:5000/saude >/dev/null 2>&1; then
  echo "OK: o painel está rodando."
else
  erro "o painel não respondeu. Veja o motivo com: journalctl -u painel-propagandas -n 50"
fi
if [ "$MODO_TESTE" -eq 1 ]; then
  if curl -fsSk --noproxy "*" --max-time 20 --resolve "$DOMINIO:443:127.0.0.1" "https://$DOMINIO/saude" >/dev/null 2>&1; then
    echo "OK: https://$DOMINIO responde (certificado de teste)."
  else
    aviso "o HTTPS de teste ainda não respondeu. Veja: journalctl -u caddy -n 50"
  fi
  IP_LOCAL="$(hostname -I | awk '{print $1}')"
  echo
  echo "No SEU computador, adicione ao arquivo hosts a linha:   $IP_LOCAL $DOMINIO"
  printf '%s\n' '  Windows: C:\Windows\System32\drivers\etc\hosts (abra o Bloco de Notas como administrador)'
  echo "  Mac/Linux: /etc/hosts"
  echo "Depois abra https://$DOMINIO e aceite o aviso do certificado de teste."
elif curl -fsS --max-time 20 "https://$DOMINIO/saude" >/dev/null 2>&1; then
  echo "OK: https://$DOMINIO está no ar com certificado válido."
else
  aviso "https://$DOMINIO ainda não respondeu. Se o DNS acabou de ser criado, aguarde alguns minutos."
  aviso "Para acompanhar: journalctl -u caddy -f"
fi

cat <<FIM

$(printf '\033[1;32m')Instalação concluída!$(printf '\033[0m')

  1. Abra https://$DOMINIO e crie o seu usuário administrador.
     Faça isso AGORA: o primeiro a abrir o endereço cria o administrador.
  2. Em "Minha conta", ative a verificação em duas etapas.
  3. Para a cobrança automática, edite a configuração e coloque a chave do Asaas:
       sudo nano $AMBIENTE
       sudo systemctl restart painel-propagandas
     No Asaas, cadastre o webhook https://$DOMINIO/webhooks/asaas
     com o token que está em ASAAS_WEBHOOK_TOKEN nesse arquivo.
  4. Configure o backup externo (docs/HOSPEDAGEM.md, passo 7).

Comandos úteis:
  sudo $PASTA/deploy/vps/atualizar.sh                  # atualizar o sistema
  sudo $PASTA/deploy/vps/gerenciar.sh listar-usuarios  # administração
  journalctl -u painel-propagandas -f                  # acompanhar os logs
FIM
