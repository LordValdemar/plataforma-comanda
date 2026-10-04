"""Repositório da Comanda no SQLite (tabelas cmd_*), sempre restrito a uma loja."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from src.domain.comanda import Comanda, Item, NovoPagamento, NumeroEmUso, Pagamento, ProdutoDoCardapio, Totais

FORMATO_BANCO = "%Y-%m-%d %H:%M:%S"  # o mesmo do CURRENT_TIMESTAMP do SQLite (UTC)


def agora_texto() -> str:
    return datetime.now(timezone.utc).strftime(FORMATO_BANCO)


def _numero_repetido(erro: sqlite3.IntegrityError, numero: int) -> None:
    """O índice único (um número só fica aberto uma vez por loja) vira NumeroEmUso; outros erros seguem como são."""
    if "UNIQUE" in str(erro) and "cmd_comandas" in str(erro):
        raise NumeroEmUso(numero) from None


class RepositorioDeComandasSQLite:
    def __init__(self, conexao: sqlite3.Connection, empresa_id: int) -> None:
        self._c = conexao
        self._empresa_id = empresa_id
        self._na_transacao = False

    # -- transação ----------------------------------------------------------------

    @contextmanager
    def transacao(self) -> Iterator[None]:
        """BEGIN IMMEDIATE: trava a gravação até o fim; se der erro no meio, nada fica gravado."""
        if self._na_transacao:  # já dentro de uma: faz parte dela
            yield
            return
        with self._c:  # commit no fim, rollback se der erro
            self._c.execute("BEGIN IMMEDIATE")
            self._na_transacao = True
            try:
                yield
            finally:
                self._na_transacao = False

    @contextmanager
    def _gravando(self) -> Iterator[None]:
        """Grava sozinho (commit) quando não está dentro de uma transação maior."""
        if self._na_transacao:
            yield
        else:
            with self._c:
                yield

    # -- leitura ---------------------------------------------------------------------

    def carregar(self, comanda_id: int) -> Comanda | None:
        linha = self._c.execute(
            "SELECT * FROM cmd_comandas WHERE id = ? AND empresa_id = ?", (comanda_id, self._empresa_id)
        ).fetchone()
        if linha is None:
            return None
        itens = [
            Item(id=i["id"], nome=i["nome"], preco_centavos=i["preco_centavos"], quantidade=i["quantidade"],
                 status=i["status"], vai_cozinha=bool(i["vai_cozinha"]))
            for i in self._c.execute(
                "SELECT id, nome, preco_centavos, quantidade, status, vai_cozinha FROM cmd_itens WHERE comanda_id = ? "
                "ORDER BY id", (comanda_id,))
        ]
        pagamentos = [
            Pagamento(id=p["id"], forma=p["forma"], valor_centavos=p["valor_centavos"], recebido_centavos=p["recebido_centavos"])
            for p in self._c.execute(
                "SELECT id, forma, valor_centavos, recebido_centavos FROM cmd_pagamentos WHERE comanda_id = ? ORDER BY id",
                (comanda_id,))
        ]
        return Comanda(
            id=linha["id"], empresa_id=linha["empresa_id"], numero=linha["numero"], status=linha["status"],
            taxa_percentual=linha["taxa_percentual"], cobrar_taxa=bool(linha["cobrar_taxa"]),
            desconto_centavos=linha["desconto_centavos"], total_centavos=linha["total_centavos"],
            garcom_id=linha["garcom_id"], itens=itens, pagamentos=pagamentos,
        )

    def comanda_do_item(self, item_id: int) -> int | None:
        linha = self._c.execute(
            "SELECT comanda_id FROM cmd_itens WHERE id = ? AND empresa_id = ?", (item_id, self._empresa_id)
        ).fetchone()
        return None if linha is None else int(linha["comanda_id"])

    def produto_do_cardapio(self, produto_id: int) -> ProdutoDoCardapio | None:
        linha = self._c.execute(
            "SELECT id, nome, preco_centavos, vai_cozinha FROM cmd_produtos WHERE id = ? AND empresa_id = ? AND ativo = 1",
            (produto_id, self._empresa_id),
        ).fetchone()
        if linha is None:
            return None
        return ProdutoDoCardapio(id=linha["id"], nome=linha["nome"], preco_centavos=linha["preco_centavos"],
                                 vai_cozinha=bool(linha["vai_cozinha"]))

    # -- gravação ----------------------------------------------------------------------

    def inserir_comanda(self, numero: int, mesa: str | None, cliente: str | None, taxa_percentual: float,
                        aberta_por: int | None, garcom_id: int | None) -> int:
        try:
            with self._gravando():
                cursor = self._c.execute(
                    "INSERT INTO cmd_comandas (empresa_id, numero, mesa, cliente, taxa_percentual, aberta_por, garcom_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (self._empresa_id, numero, mesa, cliente, taxa_percentual, aberta_por, garcom_id),
                )
        except sqlite3.IntegrityError as erro:
            _numero_repetido(erro, numero)
            raise
        return int(cursor.lastrowid or 0)

    def inserir_item(self, comanda_id: int, produto: ProdutoDoCardapio, quantidade: int, observacao: str | None,
                     status: str, lancado_por: int | None) -> int:
        agora = agora_texto()
        with self._gravando():
            cursor = self._c.execute(
                "INSERT INTO cmd_itens (empresa_id, comanda_id, produto_id, nome, preco_centavos, quantidade, observacao, "
                "vai_cozinha, status, lancado_por, lancado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self._empresa_id, comanda_id, produto.id, produto.nome, produto.preco_centavos, quantidade, observacao,
                 int(produto.vai_cozinha), status, lancado_por, agora, agora),
            )
        return int(cursor.lastrowid or 0)

    def definir_garcom_se_vazio(self, comanda_id: int, garcom_id: int) -> None:
        with self._gravando():
            self._c.execute(
                "UPDATE cmd_comandas SET garcom_id = ? WHERE id = ? AND empresa_id = ? AND garcom_id IS NULL",
                (garcom_id, comanda_id, self._empresa_id),
            )

    def gravar_dados(self, comanda_id: int, mesa: str | None, cliente: str | None, garcom_id: int | None) -> None:
        with self._gravando():
            self._c.execute(
                "UPDATE cmd_comandas SET mesa = ?, cliente = ?, garcom_id = ? WHERE id = ? AND empresa_id = ?",
                (mesa, cliente, garcom_id, comanda_id, self._empresa_id),
            )

    def mudar_situacao_do_item(self, item_id: int, nova: str) -> None:
        with self._gravando():
            self._c.execute(
                "UPDATE cmd_itens SET status = ?, atualizado_em = ? WHERE id = ? AND empresa_id = ?",
                (nova, agora_texto(), item_id, self._empresa_id),
            )

    def marcar_tudo_pronto(self, comanda_id: int) -> int:
        with self._gravando():
            alterados = self._c.execute(
                "UPDATE cmd_itens SET status = 'pronto', atualizado_em = ? "
                "WHERE empresa_id = ? AND comanda_id = ? AND vai_cozinha = 1 AND status IN ('pendente', 'preparando')",
                (agora_texto(), self._empresa_id, comanda_id),
            ).rowcount
        return int(alterados)

    def cancelar_item(self, item_id: int, motivo: str, usuario_id: int | None) -> None:
        with self._gravando():
            self._c.execute(
                "UPDATE cmd_itens SET status = 'cancelado', cancelado_por = ?, motivo_cancelamento = ?, atualizado_em = ? "
                "WHERE id = ? AND empresa_id = ?",
                (usuario_id, motivo, agora_texto(), item_id, self._empresa_id),
            )

    def inserir_pagamento(self, comanda_id: int, pagamento: NovoPagamento, usuario_id: int | None) -> int:
        with self._gravando():
            cursor = self._c.execute(
                "INSERT INTO cmd_pagamentos (empresa_id, comanda_id, forma, valor_centavos, recebido_centavos, registrado_por) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (self._empresa_id, comanda_id, pagamento.forma, pagamento.valor_centavos, pagamento.recebido_centavos,
                 usuario_id),
            )
        return int(cursor.lastrowid or 0)

    def remover_pagamento(self, comanda_id: int, pagamento_id: int) -> Pagamento | None:
        with self._gravando():
            linha = self._c.execute(
                "SELECT id, forma, valor_centavos, recebido_centavos FROM cmd_pagamentos "
                "WHERE id = ? AND comanda_id = ? AND empresa_id = ?",
                (pagamento_id, comanda_id, self._empresa_id),
            ).fetchone()
            if linha is None:
                return None
            self._c.execute("DELETE FROM cmd_pagamentos WHERE id = ?", (pagamento_id,))
        return Pagamento(id=linha["id"], forma=linha["forma"], valor_centavos=linha["valor_centavos"],
                         recebido_centavos=linha["recebido_centavos"])

    def gravar_ajuste(self, comanda_id: int, cobrar_taxa: bool, desconto: int) -> None:
        with self._gravando():
            self._c.execute(
                "UPDATE cmd_comandas SET cobrar_taxa = ?, desconto_centavos = ? WHERE id = ? AND empresa_id = ?",
                (int(cobrar_taxa), desconto, comanda_id, self._empresa_id),
            )

    def gravar_fechamento(self, comanda_id: int, totais: Totais, usuario_id: int | None) -> None:
        with self._gravando():
            self._c.execute(
                "UPDATE cmd_comandas SET status = 'fechada', total_centavos = ?, taxa_centavos = ?, desconto_centavos = ?, "
                "fechada_por = ?, fechada_em = ? WHERE id = ? AND empresa_id = ?",
                (totais.total, totais.taxa, totais.desconto, usuario_id, agora_texto(), comanda_id, self._empresa_id),
            )

    def gravar_cancelamento(self, comanda_id: int, motivo: str, usuario_id: int | None) -> None:
        agora = agora_texto()
        with self._gravando():
            self._c.execute(
                "UPDATE cmd_comandas SET status = 'cancelada', motivo_cancelamento = ?, fechada_por = ?, fechada_em = ?, "
                "total_centavos = 0 WHERE id = ? AND empresa_id = ?",
                (motivo, usuario_id, agora, comanda_id, self._empresa_id),
            )
            self._c.execute(
                "UPDATE cmd_itens SET status = 'cancelado', cancelado_por = ?, motivo_cancelamento = ?, atualizado_em = ? "
                "WHERE comanda_id = ? AND empresa_id = ? AND status != 'cancelado'",
                (usuario_id, "comanda cancelada", agora, comanda_id, self._empresa_id),
            )

    def gravar_reabertura(self, comanda_id: int, numero: int) -> None:
        try:
            with self._gravando():
                self._c.execute(
                    "UPDATE cmd_comandas SET status = 'aberta', total_centavos = NULL, taxa_centavos = NULL, "
                    "fechada_por = NULL, fechada_em = NULL WHERE id = ? AND empresa_id = ?",
                    (comanda_id, self._empresa_id),
                )
        except sqlite3.IntegrityError as erro:
            _numero_repetido(erro, numero)
            raise

    def registrar_historico(self, comanda_id: int, acao: str, detalhe: str, usuario_id: int | None) -> None:
        with self._gravando():
            self._c.execute(
                "INSERT INTO cmd_auditoria (empresa_id, usuario_id, comanda_id, acao, detalhe) VALUES (?, ?, ?, ?, ?)",
                (self._empresa_id, usuario_id, comanda_id, acao, detalhe),
            )
