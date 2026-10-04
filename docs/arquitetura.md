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
| 4. Versões locais (Comanda e Painel) usando o mesmo núcleo | a fazer |

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
