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
| 2. Comanda (dinheiro, itens, pagamentos, fechamento) | em andamento |
| 3. Permissões e autorizações, ponto, painel de propagandas | a fazer |
| 4. Versões locais (Comanda e Painel) usando o mesmo núcleo | a fazer |
