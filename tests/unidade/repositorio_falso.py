"""Repositório de comandas em memória: para testar as regras sem banco de dados."""

from contextlib import contextmanager
from dataclasses import replace
from typing import Iterator

from src.domain.comanda import Comanda, Item, NovoPagamento, NumeroEmUso, Pagamento, ProdutoDoCardapio, Totais


class RepositorioFalso:
    def __init__(self) -> None:
        self.comandas: dict[int, Comanda] = {}
        self.produtos: dict[int, ProdutoDoCardapio] = {}
        self.historico: list[tuple[int, str, str, int | None]] = []
        self.transacoes = 0
        self._proximo = 1

    def _id(self) -> int:
        self._proximo += 1
        return self._proximo

    @contextmanager
    def transacao(self) -> Iterator[None]:
        self.transacoes += 1
        copia = {k: replace(v, itens=list(v.itens), pagamentos=list(v.pagamentos)) for k, v in self.comandas.items()}
        try:
            yield
        except Exception:
            self.comandas = copia  # desfaz, como o banco
            raise

    def carregar(self, comanda_id: int) -> Comanda | None:
        comanda = self.comandas.get(comanda_id)
        return None if comanda is None else replace(comanda, itens=list(comanda.itens), pagamentos=list(comanda.pagamentos))

    def comanda_do_item(self, item_id: int) -> int | None:
        return next((c.id for c in self.comandas.values() if c.item(item_id)), None)

    def produto_do_cardapio(self, produto_id: int) -> ProdutoDoCardapio | None:
        return self.produtos.get(produto_id)

    def inserir_comanda(self, numero: int, mesa: str | None, cliente: str | None, taxa_percentual: float,
                        aberta_por: int | None, garcom_id: int | None) -> int:
        if any(c.numero == numero and c.aberta for c in self.comandas.values()):
            raise NumeroEmUso(numero)
        novo = self._id()
        self.comandas[novo] = Comanda(id=novo, empresa_id=1, numero=numero, status="aberta", taxa_percentual=taxa_percentual,
                                      cobrar_taxa=True, desconto_centavos=0, garcom_id=garcom_id)
        return novo

    def inserir_item(self, comanda_id: int, produto: ProdutoDoCardapio, quantidade: int, observacao: str | None,
                     status: str, lancado_por: int | None) -> int:
        novo = self._id()
        self.comandas[comanda_id].itens.append(
            Item(id=novo, nome=produto.nome, preco_centavos=produto.preco_centavos, quantidade=quantidade, status=status,
                 vai_cozinha=produto.vai_cozinha))
        return novo

    def definir_garcom_se_vazio(self, comanda_id: int, garcom_id: int) -> None:
        if self.comandas[comanda_id].garcom_id is None:
            self.comandas[comanda_id].garcom_id = garcom_id

    def gravar_dados(self, comanda_id: int, mesa: str | None, cliente: str | None, garcom_id: int | None) -> None:
        self.comandas[comanda_id].garcom_id = garcom_id

    def _trocar_item(self, item_id: int, **mudancas: object) -> None:
        for comanda in self.comandas.values():
            comanda.itens = [replace(i, **mudancas) if i.id == item_id else i for i in comanda.itens]  # type: ignore[arg-type]

    def mudar_situacao_do_item(self, item_id: int, nova: str) -> None:
        self._trocar_item(item_id, status=nova)

    def marcar_tudo_pronto(self, comanda_id: int) -> int:
        comanda = self.comandas[comanda_id]
        alterados = [i for i in comanda.itens if i.vai_cozinha and i.status in ("pendente", "preparando")]
        comanda.itens = [replace(i, status="pronto") if i in alterados else i for i in comanda.itens]
        return len(alterados)

    def cancelar_item(self, item_id: int, motivo: str, usuario_id: int | None) -> None:
        self._trocar_item(item_id, status="cancelado")

    def inserir_pagamento(self, comanda_id: int, pagamento: NovoPagamento, usuario_id: int | None) -> int:
        novo = self._id()
        self.comandas[comanda_id].pagamentos.append(
            Pagamento(id=novo, forma=pagamento.forma, valor_centavos=pagamento.valor_centavos,
                      recebido_centavos=pagamento.recebido_centavos))
        return novo

    def remover_pagamento(self, comanda_id: int, pagamento_id: int) -> Pagamento | None:
        comanda = self.comandas[comanda_id]
        achado = next((p for p in comanda.pagamentos if p.id == pagamento_id), None)
        comanda.pagamentos = [p for p in comanda.pagamentos if p.id != pagamento_id]
        return achado

    def gravar_ajuste(self, comanda_id: int, cobrar_taxa: bool, desconto: int) -> None:
        self.comandas[comanda_id].cobrar_taxa = cobrar_taxa
        self.comandas[comanda_id].desconto_centavos = desconto

    def gravar_fechamento(self, comanda_id: int, totais: Totais, usuario_id: int | None) -> None:
        self.comandas[comanda_id].status = "fechada"
        self.comandas[comanda_id].total_centavos = totais.total

    def gravar_cancelamento(self, comanda_id: int, motivo: str, usuario_id: int | None) -> None:
        comanda = self.comandas[comanda_id]
        comanda.status = "cancelada"
        comanda.itens = [replace(i, status="cancelado") for i in comanda.itens]

    def gravar_reabertura(self, comanda_id: int, numero: int) -> None:
        if any(c.numero == numero and c.aberta and c.id != comanda_id for c in self.comandas.values()):
            raise NumeroEmUso(numero)
        self.comandas[comanda_id].status = "aberta"
        self.comandas[comanda_id].total_centavos = None

    def registrar_historico(self, comanda_id: int, acao: str, detalhe: str, usuario_id: int | None) -> None:
        self.historico.append((comanda_id, acao, detalhe, usuario_id))
