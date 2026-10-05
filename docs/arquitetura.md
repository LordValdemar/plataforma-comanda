# Arquitetura

O sistema está sendo organizado em camadas (arquitetura limpa). A migração é feita por partes,
sempre com todos os testes passando; o que ainda não migrou continua no pacote `propagandas`.

```
src/
├── config/          variáveis de ambiente e o arquivo configuracao.env
├── domain/          regras de negócio puras: entidades, casos de uso e exceções do domínio
│                    (não importam Flask nem banco; testadas sem banco em tests/unidade)
├── infrastructure/  implementações: repositórios SQLite, Asaas, e-mail
└── interfaces/      portas de entrada: rotas HTTP
propagandas/         código que ainda não migrou (rotas, telas e módulos antigos)
tests/               testes de integração (com o app e o banco) e de unidade (só o domínio)
```

Regras:

- `domain` não importa nada de `infrastructure`, `interfaces` nem `propagandas`. Ele define o que
  precisa (por exemplo, um repositório) e a infraestrutura implementa.
- Todo o código em `src/` tem tipos conferidos pelo mypy (modo estrito).
- O CI roda, a cada envio: ruff (estilo e erros), mypy (tipos), os testes com cobertura mínima
  de 85% e a montagem da imagem Docker.

## Situação da migração

| Etapa | Situação |
|---|---|
| 1. Estrutura `src/` e configuração | feita |
| 2. Comanda (dinheiro, itens, pagamentos, fechamento) | feita |
| 3a. Permissões e autorizações por QR | feita |
| 3b. Ponto | feita |
| 3c. Painel de propagandas: propagandas, agenda e o que vai para cada TV | feita |
| 3c. Painel de propagandas: telas, grupos e conexão da TV | feita |
| 4. Versões locais (Comanda e Painel) usando o mesmo núcleo | feita |
| 5a. Cobrança e Asaas | feita |
| 5b. Login e usuários | feita |
| 6a. Cardápio da Comanda | feita |
| 6b. Relatórios (vendas da Comanda e exibições do Painel) | feita |
| 6c. Empresas: módulos, cadastro, limites do plano e abertura de loja | feita |
| 6d. Plataforma: criar, suspender e excluir empresas clientes | feita |
| 6e. Alertas, backup, tarefas de fundo e ajustes da Comanda | feita |
| 7a. Consultas das telas da Comanda fora das rotas | feita |

## Comanda

- `src/domain/dinheiro.py`: centavos ↔ texto, porcentagem com arredondamento comercial.
- `src/domain/comanda/entidades.py`: `Comanda`, `Item`, `Pagamento`, `Totais` e as regras (conta, troco,
  fechamento, cancelamento). O troco nunca chega a R$ 200,00 (a maior cédula): valor digitado errado é recusado.
- `src/domain/comanda/servico.py`: os casos de uso. Os que mexem em dinheiro rodam numa transação travada.
- `src/domain/comanda/repositorio.py`: o que o domínio precisa do banco (contrato).
- `src/infrastructure/sqlite/comandas.py`: o contrato implementado no SQLite, sempre restrito a uma loja.
- `propagandas/comanda/comandas.py`: as rotas HTTP; leem o formulário, conferem permissões e chamam o serviço.

Testes: `tests/unidade` (regras, sem banco, em milissegundos) e `tests/test_comanda_repositorio.py`
(banco de verdade: 8 caixas pagando a mesma conta ao mesmo tempo, erro no meio da gravação, isolamento entre lojas).

## Permissões e autorizações

- `src/domain/permissoes/regras.py`: as funções (`FUNCOES`), os níveis (não, sim, com autorização), os papéis
  de cada módulo e a `TabelaDePermissoes` da loja: o que cada pessoa pode, quem pode autorizar o quê.
- `src/domain/permissoes/liberacoes.py`: a `Liberacao` (o código e o que ele liberou: uma vez, por um tempo
  ou sem prazo) e os erros com a mensagem para quem usa (código vencido, já usado, o próprio código...).
- `src/domain/permissoes/servico.py`: gerar o código, usar o código, gastar e encerrar a liberação.
  O relógio e o sorteio do código são injetados, então os testes controlam a hora.
- `src/infrastructure/sqlite/permissoes.py`: tabelas `configuracoes` e `autorizacoes`, sempre restrito a uma loja.
  Marcar o código como usado é um `UPDATE ... WHERE usado_em IS NULL`: se duas pessoas leem o mesmo QR, só uma leva.
- `propagandas/permissoes.py`: as rotas, o decorador `exigir(funcao)` e o que os templates usam.

Testes: `tests/unidade/test_permissoes_dominio.py` (regras, sem banco) e `tests/test_permissoes_repositorio.py`
(banco de verdade: 8 garçons lendo o mesmo código ao mesmo tempo, isolamento entre lojas).

## Ponto

- `src/domain/horario.py`: dias da semana e o `Horario` (inclusive o turno que vira a noite), usado também
  pela agenda das propagandas.
- `src/domain/ponto/qr.py`: o QR code da loja (muda a cada 2 minutos e a cada uso, assinado com a chave
  da loja) e o código curto de 6 letras.
- `src/domain/ponto/servico.py`: entrada, saída, fechamento no fim do horário, horários, desconectar e o
  relatório de horas. Relógio e fuso injetados.
- `src/domain/tentativas.py`: limite de códigos errados por pessoa.
- `src/infrastructure/sqlite/ponto.py`: `ponto_registros`, os horários em `usuarios` e os ajustes, restrito a uma loja.
- `propagandas/ponto.py`: as rotas, a presença confirmada (na sessão) e o before_request que segura quem está sem ponto.

Testes: `tests/unidade/test_ponto_dominio.py` (regras, sem banco) e `tests/test_ponto_repositorio.py`
(banco de verdade: 8 pessoas lendo o mesmo QR ao mesmo tempo, isolamento entre lojas).

## Painel de propagandas

- `src/domain/painel/propaganda.py`: a `Propaganda` e a agenda dela (datas, dias, faixa de horário que pode virar
  a noite), os `Destinos` (telas e grupos; trocar, acrescentar ou tirar), a `Programacao` que se edita e a ordem.
- `src/domain/painel/exibicao.py`: o registro de exibição que a TV manda (o que não faz sentido é ignorado).
- `src/domain/painel/servico.py`: cadastro, edição, ações em lote, pausa geral, letreiro e a `Playlist` de cada tela.
  Os arquivos (salvar e apagar do disco) ficam com a porta de entrada.
- `src/infrastructure/sqlite/propagandas.py`: `propagandas`, `propaganda_destinos` e `exibicoes`, restrito a uma loja.
- `src/domain/painel/telas.py` e `servico_telas.py`: a `Tela`, o endereço dela, o crachá do aparelho (o banco guarda
  só o hash), o pedido de conexão pelo QR (vale 10 minutos) e o sinal de vida da TV. `ServicoDeTelas` é o lado de
  quem administra (restrito à loja); `ServicoDeConexao` é o lado da TV, que ainda não sabe de que loja é.
- `src/infrastructure/sqlite/telas.py`: os dois repositórios (o da loja e o da TV).
- `propagandas/painel.py`, `propagandas/telas.py` e `propagandas/exibicao.py`: as rotas, os cookies e os arquivos.

Testes: `tests/unidade/test_painel_dominio.py` e `test_telas_dominio.py` (regras, sem banco),
`tests/test_propagandas_repositorio.py` e `test_telas_repositorio.py` (banco de verdade: isolamento entre lojas,
TV reenviando os mesmos registros sem duplicar, conexão pelo QR de ponta a ponta, 6 TVs disputando uma tela antiga).

- `src/infrastructure/sqlite/consultas_comanda.py` (`ConsultasDaComanda`): o que as telas mostram (lista, detalhe,
  fechamento, cupom, histórico, cozinha), só leitura e sempre de uma loja; `src/domain/comanda/cozinha.py`: a
  tela da cozinha agrupada por comanda (tudo pronto vai para o fim).

## Cardápio

- `src/domain/cardapio`: categorias (em ordem) e produtos (preço, código de lançamento rápido, se vai para a
  cozinha, fora do cardápio); `src/domain/ordem.py`: subir ou descer um item numa lista (também usado nas propagandas).
- `src/infrastructure/sqlite/cardapio.py`: cmd_categorias e cmd_produtos; nome ou código repetido vira erro da regra.
- `propagandas/comanda/cardapio.py`: as rotas.

## Relatórios

- `src/domain/periodo.py`: o período de/até lido da tela (invertido é trocado, longo demais é cortado);
  também usado no relatório do ponto.
- `src/domain/relatorios/vendas.py`: o resumo de vendas da Comanda (faturamento, ticket médio, taxa de serviço,
  formas de pagamento, produtos, garçons, cancelados) e as linhas da planilha.
- `src/domain/relatorios/exibicoes.py`: o resumo de exibições do Painel (por propaganda, tela e dia) e a planilha.
- `src/infrastructure/sqlite/vendas.py` e `exibicoes.py`: as consultas, sempre de uma loja só (o Painel local
  copia `exibicoes.py`).
- `propagandas/comanda/relatorios.py` e `propagandas/relatorios.py`: as rotas e o CSV (com BOM, para o Excel).

Testes: `tests/unidade/test_relatorios_dominio.py` e `tests/test_relatorios_repositorio.py` (comanda antiga sem taxa
gravada, isolamento entre lojas, o dia local com fuso).

## Empresas

- `src/domain/empresas/modulos.py`: os módulos (Painel e Comanda) e quais a empresa usa (plano + liberados à mão;
  a principal usa todos).
- `src/domain/empresas/cadastro.py`: código da loja (formato, reservados, gerado do nome), CPF/CNPJ, telefone, CEP,
  UF, logo (PNG/JPG até 300 KB) e e-mails de alerta.
- `src/domain/empresas/limites.py`: telas e armazenamento do plano.
- `src/domain/empresas/servico.py`: salvar as configurações da loja e abrir uma loja pelo cadastro aberto (se o
  administrador for recusado, a empresa é desfeita). O webhook é conferido por quem chama (a porta de entrada
  resolve o endereço e recusa os internos).
- `src/domain/empresas/plataforma.py`: a plataforma criando empresas (com o administrador; recusado, desfaz),
  mudando limites e módulos liberados, suspendendo (a principal nunca; suspensa por atraso mantém o motivo) e
  excluindo (só com o nome exato, e depois de parar a cobrança no Asaas).
- `src/infrastructure/sqlite/empresas.py`: a tabela empresas; código disputado por duas lojas vira erro da regra.
- `propagandas/planos.py` (os serviços e os limites), `modulos.py`, `cadastro.py`, `empresa.py`, `conta.py` e
  `plataforma.py`: a porta de entrada.

Testes: `tests/unidade/test_empresas_dominio.py`, `test_plataforma_dominio.py` e `tests/test_empresas_repositorio.py`.

## Alertas, backup e tarefas de fundo

- `src/domain/alertas.py`: os canais de cada empresa (e-mail só com SMTP configurado; a principal usa os padrões
  do ambiente), quando uma tela está online e o monitor que avisa uma vez quando a tela cai e outra quando volta.
- `src/infrastructure/alertas.py`: o envio por e-mail (SMTP) e por webhook, só para endereços https públicos e sem
  seguir redirecionamentos (proteção contra SSRF); `src/infrastructure/sqlite/monitoramento.py`: telas e empresas.
- `src/domain/backup.py`: nome dos arquivos, quantos guardar (o mais novo sempre fica) e o que um .zip pode conter
  (sem caminhos como `../`); `src/infrastructure/backup.py`: criar e restaurar (as versões locais copiam).
- `src/domain/relatorios/exibicoes.py` (`inicio_da_retencao`) e `src/infrastructure/sqlite/exibicoes.py`
  (`apagar_exibicoes_anteriores`): a limpeza do histórico de exibições.
- `propagandas/alertas.py`, `backup.py` e `tarefas.py` (o laço de segundo plano): a porta de entrada.
- Os ajustes da Comanda conferem a taxa de serviço com a regra da comanda (`taxa_percentual_valida`).

Testes: `tests/unidade/test_alertas_backup_dominio.py` e `tests/test_alertas_backup_infra.py`.

## Cobrança e Asaas

- `src/domain/cobranca`: planos, a empresa cobrada, a fatura (vinda do Asaas, com o link sempre https), a regra
  de suspender e reativar por atraso (suspensão manual nunca é desfeita sozinha) e o serviço: assinar, trocar,
  cancelar, sincronizar e o webhook. O conteúdo do webhook nunca é usado como verdade: a fatura é consultada no Asaas.
- `src/domain/cobranca/gateway.py`: o que o domínio precisa do Asaas. `src/infrastructure/asaas.py` implementa
  (monta os pedidos, pagina as faturas, transforma erros em mensagens).
- `src/domain/documentos.py`: CPF e CNPJ.
- `src/infrastructure/sqlite/cobranca.py`: planos, dados de cobrança das empresas, faturas e eventos do webhook.
- `propagandas/cobranca.py`, `conta.py` (assinatura pelo cliente) e `plataforma.py`: as rotas.

Testes: `tests/unidade/test_cobranca_dominio.py` (Asaas e banco falsos, data controlada) e
`tests/test_cobranca_repositorio.py` (banco de verdade e o cliente do Asaas com o HTTP substituído).

## Login e usuários

- `src/domain/contas`: papéis, regras de nome e senha, o acesso conforme a situação da empresa (liberado,
  só a página de pagamento, suspenso) e o serviço: criar, entrar (a loja é a da senha que confere; o código
  da loja desempata), 2FA, trocar senha (derruba os outros aparelhos) e a equipe da loja.
- `src/domain/totp.py`: os códigos de 2FA (RFC 6238); `src/domain/tentativas.py`: o limite de tentativas por IP.
- `src/infrastructure/senhas.py`: o hash das senhas (werkzeug); `src/infrastructure/sqlite/contas.py`: usuarios.
- `propagandas/auth.py`: sessão, CSRF, os decoradores de login e as rotas.

Testes: `tests/unidade/test_contas_dominio.py` (senhas e banco falsos, relógio controlado) e
`tests/test_contas_repositorio.py` (banco e hash de verdade; limite de tentativas com 16 threads).

## Versões locais

A Comanda e o Painel locais (instalados no servidor do estabelecimento) usam as mesmas regras
da plataforma. Só o domínio (`src/domain`) é compartilhado: cada versão tem o próprio banco e,
por isso, a própria infraestrutura. Para levar uma mudança de regra às versões locais:

```bash
python ferramentas/copiar_nucleo.py ../Comanda ../S
```

Uma versão local com as mesmas tabelas da plataforma pode receber também arquivos de
`src/infrastructure` (escolhidos na primeira cópia com `--infra`; as seguintes lembram a lista).
O Painel local usa os repositórios das propagandas e das telas.

A cópia leva `src/nucleo.json` (commit de origem, arquivos e impressão digital) e
`tests/test_nucleo.py`, que falha se alguém mudar a cópia à mão. Regras se mudam aqui e se copiam de novo.
