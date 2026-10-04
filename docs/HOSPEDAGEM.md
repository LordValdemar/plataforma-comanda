# 🌐 Hospedagem numa VPS com HTTPS

Guia para colocar o Painel de Propagandas na internet, com endereço próprio (ex.: `https://painel.sualoja.com.br`), certificado HTTPS automático, cobrança pelo Asaas e backup fora do servidor.

**Tempo:** cerca de 1 hora na primeira vez. **Conhecimento:** copiar e colar comandos num terminal.

```
  Clientes (navegador)  ─┐
  TVs das lojas          ├──►  HTTPS (443)  ──►  Caddy  ──►  Painel (só 127.0.0.1:5000)
  Asaas (webhook)       ─┘                     certificado      dados em /opt/painel-propagandas/dados
                                               automático
```

O painel nunca fica exposto diretamente: só o Caddy recebe conexões da internet. O Caddy obtém e renova o certificado HTTPS sozinho (Let's Encrypt, gratuito).

---

## 1. O que você precisa

| Item | Recomendação |
|---|---|
| **VPS** (servidor virtual) | Ubuntu **24.04 LTS** (ou 22.04 / Debian 12), 1 vCPU, **2 GB de RAM**, 40 GB de SSD para começar |
| **Domínio** | Ex.: `sualoja.com.br` (no [registro.br](https://registro.br)) ou outro que você já tenha |
| **Conta no Asaas** | Para a cobrança automática (pode deixar para depois) |

**Escolhendo a VPS:** prefira um provedor com data center **no Brasil** (menos atraso para as TVs e dados no país). Exemplos: Magalu Cloud, Hostinger (região Brasil), Vultr (São Paulo), Locaweb. Compare os preços atuais. Os planos de entrada atendem bem dezenas de telas.

**Espaço em disco:** o que mais ocupa são os vídeos dos clientes e os backups (por padrão, 7 cópias). Some os limites de armazenamento dos planos que você vender e deixe folga. Dá para aumentar o disco depois, no painel do provedor.

> Se o provedor tiver um **firewall próprio** no painel (às vezes chamado de *security group*), libere nele as portas **22, 80 e 443**.

---

## 2. Apontar o domínio para o servidor

Ao criar a VPS, o provedor mostra o **IP público** dela (ex.: `203.0.113.25`).

No painel onde o domínio está registrado (no registro.br: *Domínio → Editar zona DNS*), crie o registro:

| Tipo | Nome | Valor |
|---|---|---|
| `A` | `painel` | `203.0.113.25` (o IP da VPS) |

Isso cria `painel.sualoja.com.br`. Pode levar de alguns minutos a algumas horas para valer. Para conferir, no seu computador:

```bash
nslookup painel.sualoja.com.br     # deve mostrar o IP da VPS
```

> **Domínio na Cloudflare** (ex.: `comercialgustavo.com.br`): o registro é criado em *DNS → Registros → Adicionar registro*, no painel da Cloudflare, e não no registro.br. Deixe a **nuvem cinza** ("Somente DNS"): o próprio servidor emite o certificado HTTPS. Para usar o domínio principal (sem `painel.` na frente), use `@` como nome, e crie também um `CNAME` chamado `www` apontando para o domínio.

---

## 3. Acessar o servidor e protegê-lo

> Este passo é o resumo para VPS. Explicações mais detalhadas (terminal, chaves SSH no Windows, fuso horário, atualizações automáticas, problemas comuns) estão em **[SERVIDOR-LINUX.md](SERVIDOR-LINUX.md)**.

### 3.1 Chave SSH (no seu computador)

A chave SSH substitui a senha e é muito mais segura. No terminal do seu computador (no Windows: PowerShell):

```bash
ssh-keygen -t ed25519 -C "seu@email.com"     # aceite o local padrão e defina uma senha para a chave
```

Ao criar a VPS, cole o conteúdo da chave **pública** (arquivo `~/.ssh/id_ed25519.pub`) no campo "Chave SSH" do provedor. Se a VPS já existe, envie a chave com:

```bash
ssh-copy-id root@203.0.113.25
```

### 3.2 Primeiro acesso e usuário administrador

```bash
ssh root@203.0.113.25

adduser admin                         # crie uma senha forte (usada pelo sudo)
usermod -aG sudo admin
mkdir -p /home/admin/.ssh
cp ~/.ssh/authorized_keys /home/admin/.ssh/
chown -R admin:admin /home/admin/.ssh
chmod 700 /home/admin/.ssh && chmod 600 /home/admin/.ssh/authorized_keys
```

### 3.3 Desligar o login por senha e o login do root

> ⚠️ **Antes**, abra **outro terminal** e confirme que `ssh admin@203.0.113.25` entra **sem pedir a senha do servidor**. Se não entrar, não continue: você ficaria trancado para fora.

```bash
# "00-" faz este arquivo ser lido primeiro: o SSH usa a PRIMEIRA regra que encontra,
# e muitas VPS trazem um 50-cloud-init.conf que liga o login por senha.
sudo tee /etc/ssh/sshd_config.d/00-seguranca.conf > /dev/null <<'FIM'
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
FIM
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin)'   # deve mostrar "no" nos dois
```

A partir de agora, entre sempre com `ssh admin@203.0.113.25`. O instalador (passo 4) completa a proteção com firewall, **fail2ban** (bloqueia quem tenta adivinhar senhas) e **atualizações automáticas de segurança**.

---

## 4. Instalar o Painel

```bash
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/LordValdemar/plataforma-comanda.git /opt/painel-propagandas
sudo /opt/painel-propagandas/deploy/vps/instalar-vps.sh painel.sualoja.com.br voce@sualoja.com.br
```

> **Repositório privado?** O `git clone` vai pedir usuário e senha. Use o seu usuário do GitHub e, como senha, um *token de acesso pessoal* (GitHub → Settings → Developer settings → Personal access tokens) com permissão só de leitura neste repositório.

O instalador:
- instala o Python, o **Caddy** (HTTPS), o firewall e o fail2ban;
- cria o usuário de sistema `painel`, que roda o serviço **sem poder alterar o próprio código**;
- cria a configuração em `/etc/painel-propagandas/ambiente` (com o token do webhook do Asaas já gerado), legível só pelo serviço;
- configura o serviço para iniciar com o servidor e reiniciar se travar;
- configura o HTTPS para o seu domínio e libera no firewall só SSH, HTTP e HTTPS;
- confere se tudo está funcionando.

Pode rodar de novo quando quiser: o que já está configurado é mantido.

---

## 5. Primeiro acesso

1. **Imediatamente**, abra `https://painel.sualoja.com.br` e crie o seu usuário administrador. Até isso ser feito, quem abrir o endereço primeiro cria o administrador.
2. Em **Minha conta**, ative a **verificação em duas etapas**.
3. Em **Telas**, cadastre as TVs. Para conectar cada TV, abra `https://painel.sualoja.com.br/tela` no navegador dela e leia o QR code que aparece com o celular (entrando como administrador ou editor). A TV fica presa à tela escolhida e funciona de qualquer lugar com internet.

---

## 6. Configurar e-mail e cobrança (Asaas)

Edite a configuração:

```bash
sudo nano /etc/painel-propagandas/ambiente
```

> Valores com espaços ou com `$` vão entre **aspas simples**. Ex.: `ASAAS_API_KEY='$aact_hmlg_...'`

**Sua marca:** ajuste `NOME_PLATAFORMA` e `CONTATO_PLATAFORMA`.

**E-mail dos alertas** (exemplo com Gmail; crie uma *senha de app* na conta Google):

```
SMTP_HOST=smtp.gmail.com
SMTP_PORTA=587
SMTP_USUARIO=voce@gmail.com
SMTP_SENHA='senha de app'
```

**Asaas (comece pelo sandbox):**
1. Na conta **sandbox** do Asaas, gere a chave de API e coloque em `ASAAS_API_KEY`, com `ASAAS_AMBIENTE=sandbox`.
2. No Asaas, em *Integrações → Webhooks*, crie um webhook com:
   - **URL:** `https://painel.sualoja.com.br/webhooks/asaas`
   - **Token:** o valor de `ASAAS_WEBHOOK_TOKEN` (já está no arquivo)
   - **Eventos:** os de cobranças (`PAYMENT_...`)

Salve (`Ctrl+O`, `Enter`, `Ctrl+X`) e reinicie:

```bash
sudo systemctl restart painel-propagandas
```

**Teste o ciclo completo no sandbox antes de cobrar de verdade:**
- [ ] Crie um plano e um cliente de teste (com um CPF válido) e ative a cobrança.
- [ ] A fatura aparece no painel do cliente e na plataforma.
- [ ] Simule o pagamento no painel do sandbox do Asaas: a fatura fica "Paga".
- [ ] Crie um cliente com vencimento passado e confirme o aviso de fatura vencida e a suspensão depois da tolerância.

Quando tudo estiver certo, gere a chave de **produção** na conta real, troque `ASAAS_API_KEY` e use `ASAAS_AMBIENTE=producao`. Cadastre o mesmo webhook na conta real e reinicie o serviço.

---

## 6.1 Plataforma de assinatura: planos, Comanda e cadastro aberto

O sistema tem dois módulos: o **Painel de Propagandas** e a **Comanda** (pedidos pelo celular dos garçons, tela da cozinha e caixa). Cada loja usa os módulos do plano que assinou. Cada loja tem os próprios usuários, separados das outras lojas: o garçom "joao" de uma lanchonete não tem nada a ver com o "joao" de outra.

### Crie os planos

Entre com o seu usuário e abra **Plataforma → Planos**. Para cada plano, informe o nome, o preço por mês e **marque os módulos** que ele inclui. Por exemplo:

| Plano | Módulos | Exemplo de preço |
|---|---|---|
| Painel | Painel de Propagandas | R$ 49,90 |
| Comanda | Comanda | R$ 79,90 |
| Completo | os dois | R$ 119,90 |

Os preços podem mudar quando quiser. Quem já assinou continua com o valor antigo até trocar de plano.

### Abra o cadastro para o público

Com o Asaas configurado (seção 6), edite a configuração:

```bash
sudo nano /etc/painel-propagandas/ambiente
```

e mude estas linhas:

```
NOME_PLATAFORMA='Comercial Gustavo'
CADASTRO_ABERTO=1
TESTE_GRATIS_DIAS=7
```

Salve e reinicie: `sudo systemctl restart painel-propagandas`.

A partir daí:

1. Quem abre o seu domínio vê a apresentação dos módulos e os **planos**.
2. A pessoa clica em **Começar**, cria a conta da loja (nome, código da loja, e-mail, usuário e senha) e escolhe o plano.
3. Ela informa o CPF ou CNPJ, e o Asaas cria a assinatura. **Os módulos liberam na hora.** A primeira fatura vence depois dos dias grátis e chega no e-mail dela.
4. Em **Minha loja**, ela troca de plano ou cancela quando quiser. Os módulos que não assinou aparecem com o botão **Assinar**.
5. Se não pagar, a loja é bloqueada automaticamente depois da tolerância (`COBRANCA_TOLERANCIA_DIAS`) e volta sozinha quando pagar.

Você recebe um aviso (e-mail ou webhook de alertas, seção 6) a cada loja nova, assinatura, troca e cancelamento.

### A equipe de cada loja

O administrador da loja cadastra a equipe em **Usuários**, escolhendo o papel de cada um:

| Papel | Módulo | Pode |
|---|---|---|
| Administrador | todos os assinados | tudo da loja, inclusive equipe e assinatura |
| Editor | Painel | propagandas |
| Garçom | Comanda | abre comandas e lança pedidos |
| Cozinha | Comanda | só a tela da cozinha |
| Caixa | Comanda | fecha contas, cancela e vê as vendas |

Cada loja tem um **endereço de entrada** próprio, mostrado em **Minha loja** (ex.: `https://comercialgustavo.com.br/entrar/padeiro-lanches`). Nele, o código da loja já vem preenchido: é o ideal para criar o ícone na tela inicial do celular da equipe.

### Lojas que você mesmo cadastra

Em **Plataforma → Nova empresa** você continua criando lojas à mão, como antes. Nesse caso, marque os módulos em **"Liberar sem plano"**: é para quem você cobra por fora do site, ou para dar acesso de cortesia. Dá para mudar depois em **Editar** de cada empresa.

> **Comanda na internet:** os garçons e a cozinha precisam de internet na loja. Se ela cair, a Comanda para até voltar. Para restaurantes movimentados, recomende uma segunda internet (por exemplo, um chip 4G no roteador).

## 7. Backup fora do servidor

O painel já faz um backup por dia em `/opt/painel-propagandas/dados/backups`. Mas se o servidor for perdido, os backups vão junto. Copie-os para fora com o [rclone](https://rclone.org), que funciona com Google Drive, Backblaze B2, Amazon S3, Dropbox e outros.

```bash
sudo apt install -y rclone
sudo rclone config
```

No `rclone config`:
1. Crie um remoto para o serviço escolhido (ex.: nome `nuvem`, tipo Google Drive). Siga as perguntas.
2. Crie um segundo remoto do tipo **`crypt`** (nome `cofre`) apontando para `nuvem:painel-backups`, com uma senha forte. **Guarde essa senha fora do servidor:** sem ela, não há como restaurar.

> O backup contém o banco inteiro: usuários, faturas e as chaves da verificação em duas etapas. Por isso, use sempre o remoto **criptografado** (`cofre`).

Teste e agende para todo dia às 4h:

```bash
sudo /opt/painel-propagandas/deploy/vps/backup-externo.sh cofre: 30
echo '0 4 * * * root /opt/painel-propagandas/deploy/vps/backup-externo.sh cofre: 30 >> /var/log/painel-backup.log 2>&1' \
  | sudo tee /etc/cron.d/painel-backup
```

(O `30` é quantos dias de backups manter na nuvem.)

**Teste a restauração de vez em quando.** Um backup que nunca foi restaurado não é garantia. Veja "Restaurar um backup" na seção 10.

---

## 8. Atualizar o sistema

Quando houver uma versão nova no repositório:

```bash
sudo /opt/painel-propagandas/deploy/vps/atualizar.sh
```

O script faz backup, baixa a versão nova, atualiza as dependências, reinicia e confere se voltou. Se algo der errado, ele mostra como voltar à versão anterior.

As atualizações de segurança do Ubuntu já são instaladas sozinhas. De vez em quando, reinicie o servidor num horário calmo (`sudo reboot`) para aplicar as do sistema.

---

## 9. Monitoramento

Cadastre um monitor gratuito (ex.: [UptimeRobot](https://uptimerobot.com) ou [Better Stack](https://betterstack.com)) para o endereço:

```
https://painel.sualoja.com.br/saude
```

Ele avisa por e-mail ou celular se o painel sair do ar. As TVs que caírem são avisadas pelo próprio painel (alertas de tela offline).

Comandos úteis no servidor:

```bash
systemctl status painel-propagandas           # está rodando?
journalctl -u painel-propagandas -f           # acompanhar os logs
journalctl -u caddy -n 50                     # logs do HTTPS
df -h /                                       # espaço em disco
sudo /opt/painel-propagandas/deploy/vps/gerenciar.sh listar-empresas
```

---

## 10. Problemas comuns

**O HTTPS não funciona (erro de certificado ou a página não abre)**
- Confira se o domínio aponta para o IP da VPS: `nslookup painel.sualoja.com.br`.
- Confira se as portas **80 e 443** estão liberadas também no **firewall do provedor**.
- Veja o motivo: `journalctl -u caddy -n 50`. O Caddy tenta de novo sozinho depois que o problema é corrigido.

**"502 Bad Gateway"**: o painel está parado. Veja o motivo com `journalctl -u painel-propagandas -n 50` e reinicie com `sudo systemctl restart painel-propagandas`.

**Esqueci a senha / perdi o celular da verificação em duas etapas**
```bash
sudo /opt/painel-propagandas/deploy/vps/gerenciar.sh trocar-senha dono
sudo /opt/painel-propagandas/deploy/vps/gerenciar.sh desativar-2fa dono
```

**O webhook do Asaas não chega**
- Confira no Asaas (*Integrações → Webhooks*) se a URL é `https://.../webhooks/asaas` e se o token é igual ao `ASAAS_WEBHOOK_TOKEN`.
- Se o Asaas pausou a fila por erros, corrija e reative a fila no painel dele.
- Enquanto isso, o painel confere as faturas no Asaas de hora em hora. Na plataforma, o botão **Atualizar faturas** força a conferência.

**Restaurar um backup**
```bash
sudo systemctl stop painel-propagandas
ls /opt/painel-propagandas/dados/backups/              # escolha o arquivo
sudo /opt/painel-propagandas/deploy/vps/gerenciar.sh restaurar /opt/painel-propagandas/dados/backups/backup-AAAAMMDD-HHMMSS.zip
sudo systemctl start painel-propagandas
```
Para um backup da nuvem: `sudo rclone copy cofre:backup-AAAAMMDD-HHMMSS.zip /opt/painel-propagandas/dados/backups/` e depois `sudo chown painel:painel /opt/painel-propagandas/dados/backups/*`.

**Mudar o painel para outra VPS**: instale na nova (passos 3 e 4), copie o backup mais recente e o arquivo `/etc/painel-propagandas/ambiente`, restaure, e só então mude o DNS para o IP novo.

---

## ✅ Checklist final de segurança

- [ ] Login por senha e login do root desligados no SSH (passo 3.3)
- [ ] Verificação em duas etapas ativada no seu usuário do painel **e** na conta do Asaas
- [ ] `ASAAS_API_KEY` só em `/etc/painel-propagandas/ambiente`, nunca no código ou no GitHub
- [ ] Se o Asaas permitir, chave de API restrita ao IP da VPS
- [ ] Backup externo criptografado agendado **e uma restauração testada**
- [ ] Monitor de disponibilidade apontando para `/saude`
- [ ] Ciclo de cobrança testado no sandbox antes de usar a produção
- [ ] Política de Privacidade e Termos revisados por um advogado
