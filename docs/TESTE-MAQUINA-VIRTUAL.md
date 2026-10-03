# 🧪 Testando numa máquina virtual

Antes de instalar na loja ou de contratar uma VPS, dá para testar tudo numa **máquina virtual (VM)** no seu computador, sem custo e sem risco. Se algo der errado, é só apagar a VM e começar de novo.

Você vai testar dois cenários na mesma VM:

| Cenário | Simula | Endereço |
|---|---|---|
| **A. Instalação local** | O computador da loja | `http://IP-DA-VM:5000` |
| **B. Instalação de VPS** | O servidor na internet, com HTTPS | `https://painel.teste` (certificado de teste) |

---

## 1. Crie a máquina virtual

Use o **VirtualBox**, que é gratuito e funciona em Windows, Mac (Intel) e Linux.

1. Instale o [VirtualBox](https://www.virtualbox.org/wiki/Downloads).
2. Baixe o **Ubuntu Server 24.04 LTS** em [ubuntu.com/download/server](https://ubuntu.com/download/server) (um arquivo `.iso`).
3. No VirtualBox, clique em **Novo**:
   - Nome: `painel-teste`; Imagem ISO: o arquivo baixado
   - Memória: **2048 MB**; Processadores: **2**; Disco: **20 GB**
4. **Antes de ligar**, abra **Configurações → Rede → Adaptador 1** e escolha **“Placa em modo Bridge”**. Assim a VM ganha um IP na sua rede, como se fosse outro computador, e o seu navegador e o seu celular conseguem acessá-la.
5. Ligue a VM e instale o Ubuntu aceitando as opções padrão. Quando perguntar, **marque “Install OpenSSH server”** e crie um usuário e uma senha.
   > Na tela **Network configuration** do instalador, o IP deve ser da sua rede (ex.: `192.168.0.x`). Se aparecer **`10.0.2.15`**, a VM está em modo **NAT**, não Bridge. Pode continuar a instalação normalmente (escolha *Concluído*). Depois que terminar, desligue a VM (`sudo poweroff`), troque a rede para **Placa em modo Bridge** e ligue de novo: o Ubuntu pega o IP novo sozinho. A opção **“Criar limite”** que aparece nessa tela é uma tradução errada de *“Create bond”* (juntar placas de rede); ignore.
6. Depois de reiniciar, entre com o seu usuário e veja o IP da VM:
   ```bash
   hostname -I        # ex.: 192.168.0.50
   ```

> **Mac com chip Apple (M1/M2/M3)** ou se preferir algo mais rápido: use o [Multipass](https://multipass.run), que cria uma VM Ubuntu com um comando: `multipass launch 24.04 --name painel-teste --cpus 2 --memory 2G --disk 20G`, depois `multipass shell painel-teste` para entrar e `multipass info painel-teste` para ver o IP.

**Dica:** do seu computador, é mais confortável usar a VM pelo terminal (dá para copiar e colar):
```bash
ssh seu-usuario@192.168.0.50
```

### Tire um “instantâneo” da VM limpa

No VirtualBox: **Máquina → Criar instantâneo** (nome: `limpa`). Se algum teste bagunçar a VM, volte a esse ponto em segundos. No Multipass: `multipass stop painel-teste && multipass snapshot painel-teste --name limpa`.

---

> Para entender cada tela do instalador do Ubuntu e os primeiros comandos, veja **[SERVIDOR-LINUX.md](SERVIDOR-LINUX.md)** (passos 0 a 4).

## 2. Baixe o projeto na VM

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/LordValdemar/S.git painel-propagandas
cd painel-propagandas
```

> **Repositório privado?** Use o seu usuário do GitHub e, como senha, um *token de acesso pessoal* (GitHub → Settings → Developer settings → Personal access tokens).

---

## 3. Cenário A: instalação local (como na loja)

```bash
sudo ./deploy/instalar-linux.sh
```

No **seu computador**, abra `http://192.168.0.50:5000` (troque pelo IP da VM) e siga o roteiro da seção 5.

**Para simular uma TV:** cadastre uma tela no painel e abra o endereço dela numa **outra janela do navegador** (ou no celular, conectado ao mesmo Wi-Fi). Aperte **F** para tela cheia.

Quando terminar, volte ao instantâneo `limpa` antes do cenário B. Os dois cenários usam a mesma porta.

---

## 4. Cenário B: instalação de VPS (com HTTPS)

O instalador de VPS tem um **modo de teste**, que usa um certificado HTTPS de teste no lugar do certificado real (que exige um domínio público):

Com a VM de volta ao instantâneo `limpa`, baixe o projeto direto na pasta usada nas VPS e instale:

```bash
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/LordValdemar/plataforma-comanda.git /opt/painel-propagandas
sudo /opt/painel-propagandas/deploy/vps/instalar-vps.sh painel.teste voce@exemplo.com --teste
```

No final, o instalador mostra uma linha como `192.168.0.50 painel.teste`. Adicione essa linha ao arquivo **hosts do seu computador** (não da VM):

- **Windows:** abra o Bloco de Notas **como administrador**, abra `C:\Windows\System32\drivers\etc\hosts`, cole a linha no final e salve.
- **Mac / Linux:** `sudo nano /etc/hosts`, cole a linha no final e salve.

Abra `https://painel.teste`. O navegador vai avisar que **a conexão não é privada**: é o esperado, porque o certificado é de teste. Clique em **Avançado → Continuar para painel.teste**. Numa VPS de verdade, com domínio real, esse aviso não aparece.

Neste cenário, você está testando exatamente o que vai rodar na VPS: o Caddy na frente, o painel só acessível localmente, o firewall, o serviço com o usuário `painel`, a configuração em `/etc/painel-propagandas/ambiente` e os scripts `atualizar.sh` e `gerenciar.sh`.

> Nunca use `--teste` numa VPS de verdade: os clientes veriam o aviso de certificado.

---

## 5. Roteiro de testes

Marque o que funcionou. Se algo falhar, anote a mensagem (e os logs: `journalctl -u painel-propagandas -n 50`).

**Básico**
- [ ] Primeiro acesso: criar o administrador e entrar
- [ ] Ativar a verificação em duas etapas (com o app autenticador no celular), sair e entrar de novo com o código
- [ ] Enviar imagens e um vídeo; reordenar; desativar uma propaganda
- [ ] Letreiro aparecendo na tela

**Telas e agendamento**
- [ ] Cadastrar 2 telas e abrir cada uma numa janela diferente
- [ ] Propaganda só para uma das telas
- [ ] Propaganda com horário (ex.: só nos próximos 10 minutos) entrando e saindo do ar sozinha
- [ ] Em **Telas**, as duas aparecem **Online**; feche uma janela e, em cerca de 3 minutos, ela aparece **Offline**

**Relatórios**
- [ ] Depois de alguns minutos com as telas abertas, **Relatórios** mostra as exibições
- [ ] Baixar o CSV e abrir no Excel

**Plataforma (venda)**
- [ ] Criar um plano e uma empresa cliente; entrar como o cliente numa janela anônima
- [ ] O cliente não vê nada da sua empresa
- [ ] Limite de telas do plano bloqueando a tela a mais
- [ ] Suspender o cliente: o painel dele bloqueia e as telas dele ficam vazias; reativar

**Cobrança (opcional, com o sandbox do Asaas)**
- [ ] Colocar a chave do **sandbox** na configuração (cenário B: `sudo nano /etc/painel-propagandas/ambiente`; depois `sudo systemctl restart painel-propagandas`)
- [ ] Ativar a cobrança de um cliente de teste e ver a fatura aparecer
- [ ] Pagar a fatura no sandbox e clicar em **Atualizar faturas** na plataforma

> Na VM, o Asaas não consegue chamar o webhook (não há endereço público). O painel busca as faturas sozinho de hora em hora, ou na hora pelo botão **Atualizar faturas**. Para testar o webhook de verdade, é preciso uma VPS ou o Cloudflare Tunnel ([INSTALACAO-LOCAL.md](INSTALACAO-LOCAL.md), seção 8).

**Robustez**
- [ ] Reiniciar a VM (`sudo reboot`): o painel volta sozinho e as telas abertas continuam passando as propagandas
- [ ] Desligar a rede da VM por 1 minuto: as telas continuam exibindo e, quando a rede volta, o relatório recebe as exibições do período
- [ ] Backup e restauração (cenário B):
  ```bash
  sudo /opt/painel-propagandas/deploy/vps/gerenciar.sh backup
  sudo systemctl stop painel-propagandas
  sudo /opt/painel-propagandas/deploy/vps/gerenciar.sh restaurar /opt/painel-propagandas/dados/backups/<arquivo>.zip
  sudo systemctl start painel-propagandas
  ```
- [ ] Atualização (cenário B): `sudo /opt/painel-propagandas/deploy/vps/atualizar.sh`

---

## 6. Problemas comuns na VM

**Não consigo abrir o painel pelo navegador do meu computador**
- Confira se a rede da VM está em **“Placa em modo Bridge”** (VirtualBox) e se o IP (`hostname -I`) está na mesma faixa do seu computador (ex.: os dois em `192.168.0.x`).
- Em redes corporativas ou de hotel, o modo bridge às vezes é bloqueado. Use então o modo **NAT** com **redirecionamento de portas** (*Configurações → Rede → Avançado → Redirecionamento de portas*): porta do hospedeiro `5000` → porta do convidado `5000` (cenário A), ou `8443` → `443` (cenário B). Acesse por `http://localhost:5000` ou `https://painel.teste:8443`, com a linha `127.0.0.1 painel.teste` no arquivo hosts.

**`https://painel.teste` não abre**: confira a linha no arquivo **hosts do seu computador** e o IP da VM. No Windows, o arquivo precisa ser salvo como administrador.

**Quero começar do zero**: volte ao instantâneo `limpa` (VirtualBox: **Máquina → Instantâneos**; Multipass: `multipass restore painel-teste.limpa`).

---

## 7. E depois?

Se tudo funcionou na VM, siga para a instalação de verdade:
- **Na loja:** [INSTALACAO-LOCAL.md](INSTALACAO-LOCAL.md)
- **Numa VPS, para vender:** [HOSPEDAGEM.md](HOSPEDAGEM.md). O passo a passo é o mesmo do cenário B, só que **sem** o `--teste` e com o seu domínio.
