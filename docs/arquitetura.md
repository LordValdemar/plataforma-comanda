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
| 3. Permissões e autorizações, ponto, painel de propagandas | a fazer |
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
