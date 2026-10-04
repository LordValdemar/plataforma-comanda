# 🏪 Instalação local (na própria loja)

Guia para rodar o Painel de Propagandas num computador da loja, sem servidor na internet. É o jeito mais barato e **continua funcionando mesmo se a internet cair**, porque tudo fica na rede interna.

```
                 rede da loja (Wi-Fi / cabo)
  ┌───────────────┐       ┌──────────────┐      ┌──────────────┐
  │  Servidor     │◄──────│  TV 1        │      │  Seu celular │
  │  (PC ou Pi)   │◄──────│  TV 2        │      │  (painel)    │
  │  porta 5000   │◄─────────────────────────────┘              │
  └───────────────┘       └──────────────┘      └──────────────┘
```

> **Quando a instalação local não basta:** TVs em **lojas diferentes** (outras redes) ou **vender para clientes**. Nesses casos, use uma VPS ([HOSPEDAGEM.md](HOSPEDAGEM.md)) ou o Cloudflare Tunnel (seção 8).

---

## 1. Escolha o computador servidor

Ele precisa ficar **ligado o tempo todo**. Qualquer uma destas opções serve:

| Opção | Bom para |
|---|---|
| **Raspberry Pi 4 ou 5** (4 GB) | Baixo consumo de energia; pode ser servidor **e** player da TV ao mesmo tempo |
| **Mini PC** (Linux ou Windows) | Mais folga para vários vídeos e muitas telas |
| **Um PC ou notebook** que já fica no caixa | Começar sem gastar nada |

Recomendações:
- **Nobreak:** quedas de energia desligam o servidor e as TVs.
- **No Raspberry Pi**, prefira um **SSD USB** a cartão de memória (cartões se desgastam com o uso contínuo). Se usar cartão, escolha um de boa qualidade e mantenha o backup em dia (seção 7).

---

## 2. Instale o painel

Baixe o projeto (GitHub → *Code → Download ZIP*, ou `git clone`) e escolha o seu sistema:

### Windows

1. Instale o **Python 3.10 ou mais novo** em [python.org](https://www.python.org/downloads/). Na instalação, marque **“Add python.exe to PATH”**.
2. Dê dois cliques em `deploy\iniciar-windows.bat`. Na primeira vez ele instala tudo (leva alguns minutos).
3. Quando o Windows perguntar sobre o **firewall**, permita o acesso em **redes privadas**. Sem isso, as TVs e o celular não conseguem acessar.
4. Para iniciar junto com o Windows: `Win + R`, digite `shell:startup` e coloque na pasta um **atalho** para o `iniciar-windows.bat`.

> A rede da loja precisa estar marcada como **Privada** no Windows (*Configurações → Rede e Internet → propriedades da rede*).

### Raspberry Pi / Linux

> Nunca configurou um servidor Linux? Prepare-o antes com o guia **[SERVIDOR-LINUX.md](SERVIDOR-LINUX.md)**: instalação do Ubuntu, atualizações, IP fixo, firewall e o que fazer para o servidor ligar sozinho depois de queda de energia.

```bash
cd painel-propagandas
sudo ./deploy/instalar-linux.sh
```

Pronto: o painel inicia sozinho sempre que o computador ligar e reinicia se travar.

### Docker (qualquer sistema)

```bash
mkdir -p dados && sudo chown 1000 dados
docker compose up -d
```

Ao iniciar, o painel mostra o endereço na rede da loja, algo como:

```
Na rede da loja (celular, outros computadores e TVs): http://192.168.0.10:5000/
```

---

## 3. Fixe o endereço do servidor (importante)

O roteador pode trocar o IP do servidor depois de uma reinicialização. Se isso acontecer, **as TVs deixam de encontrar o painel**. Para evitar:

1. Entre na página de configuração do roteador (geralmente `http://192.168.0.1` ou `http://192.168.1.1`; a senha costuma estar na etiqueta do aparelho).
2. Procure por **“Reserva de DHCP”**, **“IP fixo”** ou **“Endereço reservado”**.
3. Escolha o computador servidor na lista e reserve o IP atual dele.

---

## 4. Primeiro acesso

1. No servidor ou no celular conectado ao Wi-Fi da loja, abra o endereço mostrado no passo 2 (ex.: `http://192.168.0.10:5000`).
2. Informe o nome da empresa e crie o **usuário administrador**.
3. Em **Minha conta**, ative a verificação em duas etapas.
4. Em **Telas**, cadastre cada TV (ex.: “Balcão”, “Vitrine”). Para conectar uma TV, abra `http://192.168.0.10:5000/tela` no navegador dela e leia o QR code que aparece com o celular (entrando como administrador ou editor). A TV fica presa à tela escolhida; ao ligar de novo, o mesmo endereço `/tela` leva direto para as propagandas dela.
5. Envie as propagandas.

---

## 5. Prepare as TVs

Cada TV precisa de algo que abra o endereço dela em tela cheia. Do mais recomendado ao mais simples:

### a) Raspberry Pi ligado na TV (recomendado)

Instale o **Raspberry Pi OS com área de trabalho** e, no Pi, rode com o seu usuário (**sem** `sudo`):

```bash
sudo apt install -y git          # o Chromium já vem no Raspberry Pi OS com área de trabalho
git clone https://github.com/LordValdemar/S.git painel-propagandas
./painel-propagandas/deploy/raspberry/configurar-tela.sh http://192.168.0.10:5000/tela
sudo reboot
```

O script deixa a TV:
- abrindo sozinha em tela cheia ao ligar, com som nos vídeos;
- **esperando o servidor ligar**, se ela ligar primeiro (sem isso, a tela ficaria presa numa página de erro);
- sem apagar a tela (descanso de tela desativado);
- sem a barra “Restaurar páginas?” quando é desligada da tomada.

Ative também o login automático na área de trabalho: `sudo raspi-config` → *System Options* → *Auto Login* (em versões mais antigas, *Boot / Auto Login* → *Desktop Autologin*).

> O **mesmo Raspberry Pi** pode ser o servidor **e** o player da primeira TV: instale o painel (passo 2) e rode também o `configurar-tela.sh` com o endereço da tela.

### b) PC ou notebook com Windows ligado na TV

Abra `deploy\abrir-tela-windows.bat` no Bloco de Notas, troque o `ENDERECO` pelo endereço da tela e salve. Para abrir ao ligar, coloque um atalho dele em `shell:startup`. Ele espera o servidor responder e abre o Microsoft Edge em tela cheia.

### c) Smart TV ou TV box com navegador

Funciona para testar, mas é o menos confiável: muitas TVs apagam a tela, fecham o navegador ou bloqueiam o som e a reprodução automática. Em TV box Android existem aplicativos de “navegador quiosque” que abrem um endereço fixo em tela cheia ao ligar (são de terceiros: avalie antes de usar).

---

## 6. Configurações (marca, e-mail, Asaas)

Edite o arquivo **`configuracao.env`** na pasta do painel (o instalador cria a partir do `configuracao.env.exemplo`). No Windows, abra com o Bloco de Notas. Depois de salvar, **reinicie o painel**: no Windows, feche a janela e abra o `iniciar-windows.bat` de novo; no Linux, `sudo systemctl restart painel-propagandas`.

```ini
NOME_PLATAFORMA='Padaria Pão Quente'

# Alertas de TV desligada por e-mail (Gmail: crie uma "senha de app")
SMTP_HOST=smtp.gmail.com
SMTP_USUARIO=voce@gmail.com
SMTP_SENHA='senha de app'
ALERTA_EMAILS=voce@gmail.com
```

> O arquivo pode conter senhas e chaves: não envie para ninguém nem para o GitHub (ele já está no `.gitignore`).

**Cobrança pelo Asaas na instalação local:** funciona, com uma diferença. O Asaas não consegue avisar um computador dentro da loja (webhook), então o painel **confere as faturas de hora em hora**: bloqueios e liberações acontecem com até 1 hora de atraso. Para ser instantâneo, use o Cloudflare Tunnel (seção 8) ou uma VPS.

---

## 7. Backup

O painel faz um backup por dia sozinho, na pasta `dados/backups` (guarda os 7 últimos). Mas, se o computador quebrar ou for roubado, eles vão junto. Copie para fora:

- **Mais simples:** uma vez por semana, copie a pasta `dados/backups` para um pendrive ou HD externo.
- **Automático no Windows:** com o app do Google Drive ou do OneDrive, inclua a pasta `dados\backups` na sincronização.
- **Automático no Linux / Raspberry Pi:** use o `rclone` com o script `deploy/vps/backup-externo.sh` (veja [HOSPEDAGEM.md](HOSPEDAGEM.md), passo 7). Ele funciona igual fora de uma VPS.

Para restaurar: pare o painel e rode, na pasta dele, `.venv/bin/python gerenciar.py restaurar dados/backups/backup-AAAAMMDD-HHMMSS.zip` (no Windows: `.venv\Scripts\python gerenciar.py restaurar ...`).

---

## 8. Acessar de fora da loja (opcional)

### Só você, pelo celular, de qualquer lugar: Tailscale

O [Tailscale](https://tailscale.com) cria uma rede privada entre os seus aparelhos, sem abrir portas no roteador. Tem plano gratuito para uso pessoal.

1. Instale no servidor. No Linux/Raspberry Pi: `curl -fsSL https://tailscale.com/install.sh | sh` e depois `sudo tailscale up`. No Windows, use o instalador do site.
2. Instale o app no seu celular e entre com a mesma conta.
3. Abra `http://NOME-DO-SERVIDOR:5000` (o nome aparece no app do Tailscale).

Ninguém mais consegue acessar: o painel continua fechado para a internet.

### Endereço público com HTTPS: Cloudflare Tunnel

Use se quiser **TVs em outras lojas**, o **webhook do Asaas instantâneo** ou **clientes acessando**, mantendo o servidor na loja. O [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) liga o computador da loja a um endereço como `https://painel.sualoja.com.br`, com HTTPS, sem abrir portas no roteador. Tem plano gratuito.

1. Tenha um domínio e coloque o DNS dele na Cloudflare (cadastro gratuito no site, que explica como).
2. No painel da Cloudflare, vá em **Zero Trust → Networks → Tunnels → Create a tunnel** (tipo *Cloudflared*), escolha o sistema do servidor e **rode no servidor o comando de instalação que aparece na tela** (ele já leva o token do túnel).
3. Em **Public Hostname**, crie `painel.sualoja.com.br` apontando para o serviço `HTTP` → `localhost:5000`.
4. No `configuracao.env`, ative:
   ```ini
   ATRAS_DE_PROXY=1
   COOKIE_SEGURO=1
   ```
   e reinicie o painel.

A partir daí:
- **Para entrar no painel, use sempre `https://painel.sualoja.com.br`.** Com `COOKIE_SEGURO=1`, o login pelo endereço `http://192.168...` deixa de funcionar.
- **As TVs da loja podem continuar no endereço local** (`http://192.168...`), que não depende da internet. TVs de outras lojas usam `https://painel.sualoja.com.br/tela`.
- **Webhook do Asaas:** `https://painel.sualoja.com.br/webhooks/asaas` (veja o README, seção “Cobrança automática”).
- **Exposto à internet, a segurança importa mais:** verificação em duas etapas para todos os administradores e senhas fortes.

---

## 9. Problemas comuns

**`.venv/bin/pip: command not found` ou `ensurepip is not available` ao instalar**: falta o pacote `python3-venv`, que o Ubuntu Server e o Debian não trazem. A versão atual do instalador resolve sozinha: atualize o projeto e rode de novo:
```bash
git pull
sudo ./deploy/instalar-linux.sh
```

**A TV mostra “Tela não encontrada”**: o endereço foi digitado errado ou foi trocado em **Telas → Gerar novo endereço**. Copie o endereço de novo do painel.

**A TV não abre o painel / fica tentando conectar**
- O servidor está ligado? Abra o endereço dele no celular.
- O IP do servidor mudou? Veja o passo 3 (reserva de DHCP).
- No Windows: o firewall está bloqueando? Permita o Python em redes privadas (*Firewall do Windows Defender → Permitir um aplicativo*).

**A tela da TV apaga depois de um tempo**: no Raspberry Pi, `sudo raspi-config` → *Display Options* → *Screen Blanking* → desativar. Em PCs, desative a suspensão e o descanso de tela nas opções de energia.

**Os vídeos tocam sem som**: abra a TV pelos scripts do passo 5, que liberam o som automático. Navegadores abertos à mão bloqueiam o som até alguém clicar na tela.

**Esqueci a senha**: no servidor, na pasta do painel:
```bash
# Linux / Raspberry Pi
.venv/bin/python gerenciar.py trocar-senha NOME-DO-USUARIO
.venv/bin/python gerenciar.py desativar-2fa NOME-DO-USUARIO   # perdeu o celular da verificação em duas etapas
# Windows (no Prompt de Comando, dentro da pasta do painel)
.venv\Scripts\python gerenciar.py trocar-senha NOME-DO-USUARIO
# Docker
docker compose exec painel python gerenciar.py trocar-senha NOME-DO-USUARIO
```

---

## 10. Mudar para uma VPS depois

Se mais tarde você quiser vender ou ter lojas em lugares diferentes:

1. Instale na VPS seguindo [HOSPEDAGEM.md](HOSPEDAGEM.md).
2. Copie o backup mais recente (`dados/backups/backup-....zip`) para a VPS e restaure.
3. Copie as configurações do `configuracao.env` para `/etc/painel-propagandas/ambiente`.
4. Em cada TV, abra `https://seu-dominio/tela` e conecte de novo pelo QR code.
