# 🌐 Plataforma Comercial Gustavo (Painel de Propagandas + Comanda)

Versão **online** do sistema, para rodar numa VPS com domínio próprio (ex.: `comercialgustavo.com.br`): cada loja cria a conta, assina um plano e usa os módulos dele, com os próprios usuários.

| Repositório | Para quê |
|---|---|
| **plataforma-comanda** (este) | Site online de assinatura: Painel + Comanda + cobrança pelo Asaas |
| [Comanda](https://github.com/LordValdemar/Comanda) | Comanda **local**, no servidor da loja (funciona sem internet) |
| [S](https://github.com/LordValdemar/S) | Painel de Propagandas **local** |

Os três são independentes: atualizar um não muda os outros. Esta plataforma nasceu do código do Painel de Propagandas (a história dos commits foi mantida).

Instalação na VPS: [docs/HOSPEDAGEM.md](docs/HOSPEDAGEM.md) (seção 6.1 para os planos, a Comanda e o cadastro aberto).

## Plataforma de assinatura: Painel + Comanda

O sistema tem dois **módulos**, e cada loja usa os do plano que assinou:

- **Painel de Propagandas**: as propagandas nas TVs (tudo descrito abaixo).
- **Comanda**: os garçons lançam os pedidos pelo celular, a cozinha acompanha numa tela que se atualiza sozinha (com aviso sonoro), e o caixa fecha a conta com taxa de serviço, desconto, conta dividida, troco e cupom de 80 mm. Tem também relatórios de vendas e histórico de cancelamentos e descontos.

Com `CADASTRO_ABERTO=1`, a página inicial mostra os planos, e qualquer loja **cria a conta e assina pelo site** (Asaas: PIX, boleto ou cartão, com dias grátis configuráveis). A loja troca de plano ou cancela em **Minha loja**. Cada loja tem os **próprios usuários** (administrador, editor, garçom, cozinha, caixa), separados das outras, e um endereço de entrada próprio (`/entrar/codigo-da-loja`). O passo a passo está em [docs/HOSPEDAGEM.md](docs/HOSPEDAGEM.md), seção 6.1.

## Recursos

**Para quem usa**
- Painel no navegador (computador ou celular) para enviar imagens e vídeos, definir o tempo de cada um, mudar a ordem, ativar ou desativar e excluir.
- **Agendamento**: período de validade (ex.: 01/10 a 15/10), **dias da semana** e **faixa de horário** (ex.: café da manhã de seg a sex, das 06:00 às 10:00).
- **Várias telas e grupos de telas**: cada propaganda vai para todas as telas, para telas específicas ou para grupos (ex.: “Lojas de SP”).
- **Letreiro** com texto rolando no rodapé, geral ou próprio de cada tela.
- Tela de exibição em tela cheia que recebe as mudanças sozinha, pré-carrega as mídias e continua passando as propagandas se a rede cair.

**Monitoramento e relatórios**
- Status de cada tela em tempo real: **online/offline**, último contato, IP e **o que está exibindo agora**.
- **Alertas** por e-mail e/ou webhook (Slack, Teams, Discord, Google Chat) quando uma tela cai e quando ela volta.
- **Relatório de exibições** (proof of play): quantas vezes e por quanto tempo cada propaganda passou em cada tela, com filtro por período e tela e **exportação CSV** para o Excel. Serve para prestar contas a anunciantes.
- As exibições são guardadas na própria TV quando a rede cai e enviadas quando ela volta, sem duplicar.

**Para vender como serviço (multiempresa)**
- Cada cliente é uma **empresa** com dados totalmente isolados: propagandas, telas, grupos, usuários, relatórios e alertas.
- **Painel da plataforma** (só para você): criar clientes, definir o **plano** (limite de telas e de armazenamento), **suspender** por falta de pagamento (o painel é bloqueado e as TVs ficam sem propagandas, sem perder dados) e excluir.
- Visão geral de todos os clientes: telas (online), propagandas, armazenamento e usuários.
- **Cobrança automática pelo Asaas**: planos com preço, assinatura mensal (PIX, boleto ou cartão), faturas atualizadas por webhook, **bloqueio automático por atraso** e **liberação automática** quando o cliente paga.
- Cada cliente configura os próprios alertas e pode **exportar todos os dados** (portabilidade, LGPD).
- **Modelos** de Política de Privacidade e Termos de Uso, que precisam de revisão jurídica.

**Para quem instala e mantém**
- Servidor de produção (**Waitress**), que funciona em Linux, Windows, macOS e Raspberry Pi.
- **Login com usuários e papéis**: *Administrador* (gerencia usuários) e *Editor* (cuida das propagandas).
- Senhas guardadas com hash forte (scrypt). Trocar a senha desconecta os outros aparelhos.
- **Verificação em duas etapas (2FA)** com aplicativo autenticador (Google Authenticator, Microsoft Authenticator, Authy…). Cada código só vale uma vez.
- Bloqueio de login após 5 tentativas erradas em 15 minutos.
- Proteção **CSRF**, cookies `HttpOnly`/`SameSite` e cabeçalhos de segurança (**CSP**, anti-clickjacking).
- Arquivos enviados são conferidos **pelo conteúdo**, não só pela extensão, e salvos com nome aleatório.
- Banco **SQLite** com migrações versionadas.
- **Backup automático diário** (banco + mídias), guardando os últimos 7, com restauração por comando.
- **Logs de auditoria** (quem enviou, alterou ou excluiu o quê, logins e tentativas erradas), guardados por 190 dias, conforme o Marco Civil da Internet.
- Endereço `/saude` para monitoramento.
- Início automático com o computador (systemd no Linux, script no Windows), **Docker** e modo quiosque para a TV.
- Testes automáticos a cada envio ao GitHub (**GitHub Actions**, Python 3.10 a 3.13 + imagem Docker).

## Instalação rápida

Precisa do **Python 3.10 ou mais novo** ([python.org](https://www.python.org/downloads/)).

> **Guias passo a passo:** [configurando o servidor Linux](docs/SERVIDOR-LINUX.md) (do zero, para iniciantes); [teste numa máquina virtual](docs/TESTE-MAQUINA-VIRTUAL.md) (comece por aqui, sem custo e sem risco); [instalação na loja (local)](docs/INSTALACAO-LOCAL.md), incluindo Raspberry Pi na TV, Windows e acesso de fora; e [hospedagem numa VPS](docs/HOSPEDAGEM.md), para vender.

### Servidor na internet (VPS), para vender

Siga o guia **[docs/HOSPEDAGEM.md](docs/HOSPEDAGEM.md)**: HTTPS automático, firewall, atualizações de segurança, cobrança pelo Asaas e backup externo. Resumo:

```bash
sudo git clone <este-repositório> /opt/painel-propagandas
sudo /opt/painel-propagandas/deploy/vps/instalar-vps.sh painel.sualoja.com.br voce@sualoja.com.br
```

### Linux / Raspberry Pi (recomendado, como serviço)

```bash
git clone <este-repositório> painel-propagandas
cd painel-propagandas
sudo ./deploy/instalar-linux.sh
```

Pronto: o painel inicia sozinho sempre que o computador ligar e reinicia se travar.

### Docker

```bash
mkdir -p dados && sudo chown 1000 dados   # o container roda como usuário sem privilégios (uid 1000)
docker compose up -d
docker compose exec painel python gerenciar.py listar-usuarios
```

### Windows

Dê dois cliques em `deploy\iniciar-windows.bat`. Na primeira vez ele instala tudo.

Para iniciar junto com o Windows: aperte `Win + R`, digite `shell:startup` e coloque um **atalho** para o `iniciar-windows.bat` na pasta que abrir.

### Manual (qualquer sistema)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python servidor.py
```

## Primeiro acesso

1. Abra **http://localhost:5000/** (ou `http://IP-DO-COMPUTADOR:5000/` de outro aparelho da rede).
2. Informe o nome da sua empresa e crie o **usuário administrador**. Essa tela só aparece uma vez. Esse usuário administra a sua empresa **e** a plataforma.
3. Em **Telas**, cadastre cada TV (ex.: “Loja Centro: Balcão”). Para conectar, abra `http://192.168.0.10:5000/tela` na TV e leia o QR code com o celular (administrador ou editor). A TV fica presa à tela escolhida.
4. Envie as propagandas. Em **Agendamento e telas**, escolha dias, horários e em quais telas cada uma aparece.
5. Na TV, clique (ou aperte **F**) para tela cheia.
6. Em **Minha conta**, ative a **verificação em duas etapas**.

> Cada tela funciona só no aparelho conectado a ela: abrir o endereço em outro aparelho leva para a página do QR code. Para trocar a TV, conecte a nova pelo QR code (a anterior é desligada) ou use **Desconectar aparelho** em **Telas**.
>
> O endereço geral `/player` continua funcionando, mas mostra só as propagandas da **sua** empresa marcadas para “Todas as telas” e **não** aparece no monitoramento nem nos relatórios.

## Vendendo para clientes

1. Em **Plataforma → Nova empresa**, informe o nome do cliente, o plano (limite de telas e de armazenamento em MB; em branco = sem limite) e o usuário e senha do administrador do cliente.
2. Envie o endereço do painel, o usuário e a senha para o cliente. Ele entra, troca a senha, ativa a 2FA, cadastra as telas e envia as propagandas, sem ver nada das outras empresas.
3. Para mudar de plano, edite os limites. Se o cliente não pagar, desmarque **Ativa** para suspender. Para reativar, marque de novo.
4. Ao encerrar o contrato, peça que o cliente exporte os dados (**Empresa → Exportar**) e então exclua a empresa digitando o nome dela.

**Antes de vender, confira:**
- [ ] Servidor na internet (VPS ou nuvem) com **HTTPS** (veja “Acesso pela internet”) e backup copiado para **fora** do servidor.
- [ ] `NOME_PLATAFORMA` e `CONTATO_PLATAFORMA` configurados (aparecem nas páginas legais).
- [ ] **Política de Privacidade** (`/privacidade`) e **Termos de Uso** (`/termos`) revisados por um advogado e completados com razão social, CNPJ, preços e foro. Os textos ficam em `propagandas/templates/privacidade.html` e `termos.html`.
- [ ] Contrato de prestação de serviço e emissão de nota fiscal (o Asaas pode emitir a nota de serviço automaticamente; configure no painel dele).
- [ ] Cobrança testada no **sandbox** do Asaas antes de mudar para `ASAAS_AMBIENTE=producao`.
- [ ] Servidor de e-mail (SMTP) configurado, se os clientes forem receber alertas por e-mail.
- [ ] 2FA ativada no seu usuário, que administra a plataforma.

Sem o Asaas configurado, a cobrança é manual: você cobra por fora e suspende ou reativa pelo painel. Com o Asaas, tudo isso é automático (veja abaixo).

## Cobrança automática (Asaas)

### 1. Configurar (uma vez)

1. Crie uma conta no [Asaas](https://www.asaas.com). Para testar sem dinheiro de verdade, crie também uma conta no **sandbox** ([sandbox.asaas.com](https://sandbox.asaas.com)).
2. Em **Integrações → Chave de API**, gere a chave. A do sandbox começa com `$aact_hmlg_` e a de produção com `$aact_prod_`.
3. Em **Integrações → Webhooks**, crie um webhook:
   - **URL:** `https://SEU-DOMINIO/webhooks/asaas` (precisa ser acessível pela internet, com HTTPS);
   - **Token de autenticação:** uma sequência aleatória de 32 a 255 caracteres. Para gerar uma: `python -c "import secrets; print(secrets.token_urlsafe(40))"`;
   - **Eventos:** os de **cobranças** (`PAYMENT_CREATED`, `PAYMENT_UPDATED`, `PAYMENT_CONFIRMED`, `PAYMENT_RECEIVED`, `PAYMENT_OVERDUE`, `PAYMENT_DELETED`, `PAYMENT_REFUNDED`).
4. Configure o servidor:

```bash
ASAAS_API_KEY='$aact_hmlg_...'      # use aspas simples: a chave começa com $
ASAAS_AMBIENTE=sandbox              # troque para "producao" quando for cobrar de verdade
ASAAS_WEBHOOK_TOKEN=o-mesmo-token-do-passo-3
COBRANCA_TOLERANCIA_DIAS=5          # dias de atraso antes de bloquear
```

### 2. Usar

1. Em **Plataforma → Planos**, crie os planos (ex.: Básico, R$ 49,90, 3 telas; Pro, R$ 99,90, 10 telas).
2. No cliente, abra **Cobrança**, escolha o plano, informe o **CPF ou CNPJ** e o e-mail de cobrança, marque **Bloquear por atraso** e salve.
3. Clique em **Ativar cobrança no Asaas** e escolha o primeiro vencimento. Pronto: o Asaas gera uma fatura por mês e avisa o cliente por e-mail. O cliente escolhe PIX, boleto ou cartão.

**O que acontece sozinho:**
- Fatura vencida aparece como aviso no painel do cliente, com o link para pagar.
- Passada a tolerância, o cliente é **suspenso**: as telas ficam sem propagandas e, ao entrar, ele vê só a página de pagamento.
- Quando o Asaas confirma o pagamento, o cliente é **reativado** na hora.
- O aviso do webhook **nunca é usado como verdade**: a cada aviso, o sistema consulta a fatura direto na API do Asaas. Mesmo quem descobrir o token do webhook não consegue liberar um cliente com um aviso falso.
- Você recebe um aviso (pelos canais de alerta da sua empresa) a cada suspensão e reativação.
- De hora em hora, o sistema confere as faturas no Asaas, caso algum aviso do webhook tenha se perdido.
- Mudar o plano de um cliente atualiza o valor da assinatura no Asaas, inclusive das faturas ainda não pagas.

### Segurança da chave do Asaas

A chave de API (`ASAAS_API_KEY`) dá acesso à sua conta do Asaas, então trate-a como a senha do banco:
- guarde-a só na configuração do servidor, **nunca** no código ou no GitHub;
- ative a verificação em duas etapas na sua conta do Asaas;
- se o Asaas oferecer, restrinja a chave ao IP do seu servidor e exija confirmação para transferências;
- se suspeitar de vazamento, gere uma nova chave no painel do Asaas e atualize o servidor.

Os dados de cartão, PIX e boleto **nunca** passam pelo seu servidor: o cliente paga na página do próprio Asaas.

Uma suspensão feita **manualmente** por você nunca é desfeita por um pagamento. Para dar alguns dias a mais a um cliente em atraso, desmarque **Bloquear por atraso** e reative.

> Para descobrir o IP: no Linux, `hostname -I`; no Windows, `ipconfig` (procure “Endereço IPv4”).

## TV em modo quiosque (abre sozinha, em tela cheia)

No computador ligado à TV (ex.: Raspberry Pi com Raspberry Pi OS):

```bash
mkdir -p ~/.config/autostart
cp deploy/tela-quiosque.desktop ~/.config/autostart/
```

Edite o arquivo e troque `http://localhost:5000/player` por `http://localhost:5000/tela`. Na primeira vez, conecte a TV pelo QR code; depois ela abre direto nas propagandas. Se o servidor estiver em outro computador, use o IP dele.

O modo quiosque também libera o **som dos vídeos**. Sem ele, os navegadores bloqueiam o som automático e os vídeos tocam mudos.

## Administração pela linha de comando

```bash
python gerenciar.py listar-empresas
python gerenciar.py criar-empresa "Mercado Bom Preço" --limite-telas 5 --limite-mb 2000
python gerenciar.py listar-usuarios
python gerenciar.py criar-usuario joao --empresa 2 --papel editor
python gerenciar.py trocar-senha dono        # esqueceu a senha? use este
python gerenciar.py desativar-2fa dono       # perdeu o celular? use este
python gerenciar.py backup                   # backup na hora
python gerenciar.py restaurar dados/backups/backup-20261001-030000.zip
```

> **Antes de restaurar, pare o servidor** (`sudo systemctl stop painel-propagandas` no Linux). Os dados atuais não são apagados: ficam guardados em `dados/antes-da-restauracao-<data>/`.

## Alertas de tela offline

Cada empresa define quem recebe os alertas em **Empresa** (e-mails e/ou webhook). Depois use **Telas → Enviar alerta de teste** para conferir.

O servidor de e-mail (SMTP) é da plataforma e vem das variáveis de ambiente abaixo. Na sua empresa (a principal), `ALERTA_EMAILS` e `ALERTA_WEBHOOK` valem quando a página **Empresa** está em branco.

**Webhook** (Slack, Microsoft Teams, Discord, Google Chat ou qualquer serviço que receba JSON):

```bash
ALERTA_WEBHOOK=https://hooks.slack.com/services/XXX/YYY/ZZZ
```

**E-mail** (exemplo com Gmail; crie uma “senha de app” na sua conta Google):

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORTA=587
SMTP_USUARIO=voce@gmail.com
SMTP_SENHA=sua-senha-de-app
ALERTA_EMAILS=dono@loja.com.br,gerente@loja.com.br
```

O alerta é enviado uma vez quando a tela fica mais de `ALERTA_OFFLINE_MIN` minutos (padrão 5) sem comunicação, e outra vez quando ela volta.

## Onde ficam os dados

Tudo fica na pasta `dados/`. **É ela que você deve copiar** para levar o sistema a outro computador.

```
dados/
├── banco.sqlite3     # propagandas, usuários e configurações
├── midia/            # imagens e vídeos enviados
├── backups/          # backups automáticos (um por dia, guarda os 7 últimos)
├── logs/painel.log   # registro de acessos e alterações
└── chave_secreta     # chave das sessões (não compartilhe)
```

Os backups ficam no mesmo disco. Para proteção contra defeito no computador, copie a pasta `dados/backups/` de vez em quando para um pendrive ou para a nuvem.

## Configurações (variáveis de ambiente)

Defina como variáveis de ambiente **ou** no arquivo `configuracao.env` na pasta do painel (copie de `configuracao.env.exemplo`). As variáveis de ambiente têm prioridade sobre o arquivo.

| Variável | Padrão | Descrição |
|---|---|---|
| `PORTA` | `5000` | Porta do servidor |
| `HOST` | `0.0.0.0` | `0.0.0.0` aceita a rede local; `127.0.0.1` aceita só o próprio computador |
| `PASTA_DADOS` | `./dados` | Onde ficam banco, mídias, backups e logs |
| `TAMANHO_MAX_MB` | `500` | Tamanho máximo de cada envio |
| `BACKUP_MANTER` | `7` | Quantos backups diários guardar (`0` desliga o backup automático) |
| `FUSO_HORARIO` | `America/Sao_Paulo` | Fuso usado no agendamento e nos relatórios (ex.: `America/Manaus`) |
| `RETER_EXIBICOES_DIAS` | `365` | Por quantos dias guardar o histórico de exibições |
| `LOGS_DIAS` | `190` | Por quantos dias guardar os logs de acesso (o Marco Civil exige no mínimo 6 meses) |
| `NOME_PLATAFORMA` | `Painel de Propagandas` | Nome exibido no topo e nas páginas legais (sua marca) |
| `CONTATO_PLATAFORMA` | (vazio) | E-mail ou telefone de suporte, exibido nas páginas legais |
| `ASAAS_API_KEY` | (vazio) | Chave da API do Asaas. Sem ela, a cobrança é manual |
| `ASAAS_AMBIENTE` | `sandbox` | `sandbox` (testes) ou `producao` |
| `ASAAS_WEBHOOK_TOKEN` | (vazio) | Token que o Asaas envia no webhook (o mesmo cadastrado no Asaas) |
| `COBRANCA_TOLERANCIA_DIAS` | `5` | Dias de atraso tolerados antes de suspender o cliente |
| `ALERTA_OFFLINE_MIN` | `5` | Minutos sem comunicação até alertar |
| `ALERTA_WEBHOOK` | (vazio) | URL do webhook de alertas |
| `ALERTA_EMAILS` | (vazio) | E-mails que recebem alertas, separados por vírgula |
| `SMTP_HOST`, `SMTP_PORTA`, `SMTP_USUARIO`, `SMTP_SENHA`, `SMTP_REMETENTE` | (vazio), `587` | Servidor de e-mail. Porta 465 usa SSL; as outras usam STARTTLS |
| `COOKIE_SEGURO` | desligado | `1` quando o acesso for por **HTTPS** |
| `ATRAS_DE_PROXY` | desligado | `1` quando houver Caddy/Nginx na frente |
| `PROXY_CONFIAVEL` | `127.0.0.1` | Endereço do proxy cujos cabeçalhos `X-Forwarded-*` são aceitos (mude só se o proxy rodar em outra máquina ou contêiner) |
| `CHAVE_SECRETA` | gerada sozinha | Chave das sessões (opcional) |

No Linux com o serviço, coloque as variáveis no arquivo `/etc/systemd/system/painel-propagandas.service` (linhas `Environment=`) e rode `sudo systemctl daemon-reload && sudo systemctl restart painel-propagandas`.

## Acesso pela internet (HTTPS)

Na rede local, o acesso direto já é suficiente. **Se o painel for aberto pela internet, use HTTPS**, senão a senha trafega sem criptografia.

> Numa VPS, o instalador do guia **[docs/HOSPEDAGEM.md](docs/HOSPEDAGEM.md)** já faz tudo isto sozinho.

Para configurar à mão, o jeito mais simples é o [Caddy](https://caddyserver.com), que obtém o certificado sozinho. Veja o exemplo em `deploy/Caddyfile.exemplo` e rode o painel com:

```bash
HOST=127.0.0.1 ATRAS_DE_PROXY=1 COOKIE_SEGURO=1 python servidor.py
```

## Desenvolvimento

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check .                                      # estilo e erros comuns
python -m pytest                                  # testes automáticos
flask --app propagandas run --debug               # servidor com recarga automática
```

### Estrutura

```
servidor.py              # inicia em produção (Waitress + tarefas em segundo plano)
gerenciar.py             # comandos de administração
propagandas/
├── __init__.py          # create_app: configuração, logs, segurança
├── db.py                # SQLite e migrações (para mudar o banco, adicione em MIGRACOES)
├── auth.py              # login, 2FA, usuários, papéis, CSRF, limite de tentativas
├── totp.py              # códigos da verificação em duas etapas (RFC 6238)
├── empresa.py           # configurações da empresa e exportação dos dados
├── plataforma.py        # administração das empresas clientes
├── planos.py            # limites de telas e armazenamento
├── cobranca.py          # planos com preço, assinaturas, faturas, webhook e bloqueio por atraso
├── asaas.py             # cliente da API do Asaas
├── legal.py             # páginas de privacidade e termos
├── painel.py            # cadastro das propagandas (agendamento e destinos)
├── agenda.py            # fuso horário e regras de agendamento
├── telas.py             # telas, grupos e monitoramento
├── exibicao.py          # player, API das TVs (playlist e pulso), /midia, /saude
├── alertas.py           # alertas de tela offline (e-mail e webhook)
├── relatorios.py        # relatório de exibições e CSV
├── tarefas.py           # segundo plano: monitoramento, backup, limpeza
├── midia.py             # identificação dos arquivos pelo conteúdo
├── backup.py            # backup e restauração
├── templates/           # páginas HTML
└── static/              # CSS e JavaScript
deploy/                  # serviço systemd, instalador, Windows, quiosque, Caddy
deploy/vps/              # instalação em VPS: instalador, atualização, administração, backup externo
docs/HOSPEDAGEM.md       # guia de hospedagem com HTTPS
docs/INSTALACAO-LOCAL.md # guia de instalação na loja (Windows, Raspberry Pi, TVs, acesso de fora)
docs/TESTE-MAQUINA-VIRTUAL.md # como testar tudo numa máquina virtual
docs/SERVIDOR-LINUX.md   # como preparar um servidor Ubuntu do zero
deploy/raspberry/        # transforma um Raspberry Pi no player de uma TV
configuracao.env.exemplo # modelo do arquivo de configuração (copie para configuracao.env)
Dockerfile, docker-compose.yml
.github/workflows/       # testes automáticos no GitHub
tests/                   # testes automáticos (pytest)
```
