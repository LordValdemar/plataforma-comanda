"""O que as telas da Comanda mostram (só leitura), sempre de uma loja. As mudanças passam pelo ServicoDeComandas."""

import sqlite3
from typing import Any

Linha = dict[str, Any]


class ConsultasDaComanda:
    def __init__(self, conexao: sqlite3.Connection, empresa_id: int) -> None:
        self._c = conexao
        self._empresa_id = empresa_id

    def _linhas(self, sql: str, *parametros: object) -> list[Linha]:
        return [dict(linha) for linha in self._c.execute(sql, parametros)]

    def _linha(self, sql: str, *parametros: object) -> Linha | None:
        linha = self._c.execute(sql, parametros).fetchone()
        return None if linha is None else dict(linha)

    # -- uma comanda --------------------------------------------------------------------

    def comanda(self, comanda_id: int) -> Linha | None:
        """Só comandas da própria loja: a de outra loja dá None."""
        return self._linha(
            "SELECT c.*, u.usuario AS garcom_nome FROM cmd_comandas c LEFT JOIN usuarios u ON u.id = c.garcom_id "
            "WHERE c.id = ? AND c.empresa_id = ?", comanda_id, self._empresa_id)

    def itens(self, comanda_id: int) -> list[Linha]:
        return self._linhas(
            "SELECT i.*, u.usuario AS garcom FROM cmd_itens i LEFT JOIN usuarios u ON u.id = i.lancado_por "
            "WHERE i.comanda_id = ? AND i.empresa_id = ? ORDER BY i.id", comanda_id, self._empresa_id)

    def itens_do_cupom(self, comanda_id: int) -> list[Linha]:
        """Itens iguais (nome e preço) somados numa linha, na ordem em que foram lançados."""
        return self._linhas(
            "SELECT nome, preco_centavos, SUM(quantidade) AS quantidade FROM cmd_itens "
            "WHERE comanda_id = ? AND empresa_id = ? AND status != 'cancelado' GROUP BY nome, preco_centavos "
            "ORDER BY MIN(id)", comanda_id, self._empresa_id)

    def pagamentos(self, comanda_id: int) -> list[Linha]:
        return self._linhas("SELECT * FROM cmd_pagamentos WHERE comanda_id = ? AND empresa_id = ? ORDER BY id",
                            comanda_id, self._empresa_id)

    def itens_na_cozinha(self, comanda_id: int) -> int:
        linha = self._c.execute(
            "SELECT COUNT(*) FROM cmd_itens WHERE comanda_id = ? AND empresa_id = ? "
            "AND status IN ('pendente', 'preparando', 'pronto')", (comanda_id, self._empresa_id)).fetchone()
        return int(linha[0])

    def auditoria(self, comanda_id: int) -> list[Linha]:
        return self._linhas(
            "SELECT a.*, u.usuario FROM cmd_auditoria a LEFT JOIN usuarios u ON u.id = a.usuario_id "
            "WHERE a.comanda_id = ? AND a.empresa_id = ? ORDER BY a.id", comanda_id, self._empresa_id)

    # -- listas -------------------------------------------------------------------------

    def aberta_com_numero(self, numero: str) -> int | None:
        linha = self._c.execute("SELECT id FROM cmd_comandas WHERE empresa_id = ? AND numero = ? AND status = 'aberta'",
                                (self._empresa_id, numero)).fetchone()
        return None if linha is None else int(linha["id"])

    def abertas(self) -> list[Linha]:
        """As comandas abertas, com o consumo e quantos itens estão em cada etapa da cozinha."""
        return self._linhas(
            """
            SELECT c.*,
                   (SELECT COALESCE(SUM(preco_centavos * quantidade), 0) FROM cmd_itens
                     WHERE comanda_id = c.id AND status != 'cancelado') AS consumo,
                   (SELECT COUNT(*) FROM cmd_itens WHERE comanda_id = c.id AND status = 'pronto') AS prontos,
                   (SELECT COUNT(*) FROM cmd_itens WHERE comanda_id = c.id AND status = 'preparando') AS preparando,
                   (SELECT COUNT(*) FROM cmd_itens WHERE comanda_id = c.id AND status = 'pendente') AS aguardando,
                   (SELECT usuario FROM usuarios WHERE id = c.garcom_id) AS garcom_nome
            FROM cmd_comandas c WHERE c.empresa_id = ? AND status = 'aberta' ORDER BY numero
            """, self._empresa_id)

    def prontos_para_entregar(self) -> list[Linha]:
        return self._linhas(
            "SELECT i.*, c.numero, c.mesa FROM cmd_itens i JOIN cmd_comandas c ON c.id = i.comanda_id "
            "WHERE i.empresa_id = ? AND i.status = 'pronto' AND c.status != 'cancelada' ORDER BY i.atualizado_em",
            self._empresa_id)

    def garcons(self) -> list[Linha]:
        return self._linhas("SELECT id, usuario FROM usuarios WHERE empresa_id = ? AND papel = 'garcom' ORDER BY usuario",
                            self._empresa_id)

    def produto_pelo_codigo(self, codigo: str) -> int | None:
        """O produto à venda com este código de lançamento rápido."""
        linha = self._c.execute("SELECT id FROM cmd_produtos WHERE empresa_id = ? AND codigo = ? AND ativo = 1",
                                (self._empresa_id, codigo)).fetchone()
        return None if linha is None else int(linha["id"])

    def encerradas(self, de: str, ate: str) -> list[Linha]:
        """Histórico: fechadas e canceladas no período, com quem fechou e quem autorizou (se precisou)."""
        return self._linhas(
            "SELECT c.*, u.usuario AS fechada_por_nome, (SELECT usuario FROM usuarios WHERE id = c.garcom_id) AS garcom_nome, "
            "(SELECT a.detalhe FROM cmd_auditoria a WHERE a.comanda_id = c.id "
            "AND a.acao = 'fechar conta' ORDER BY a.id DESC LIMIT 1) AS autorizacao "
            "FROM cmd_comandas c LEFT JOIN usuarios u ON u.id = c.fechada_por "
            "WHERE c.empresa_id = ? AND c.status != 'aberta' AND c.fechada_em >= ? AND c.fechada_em < ? "
            "ORDER BY c.fechada_em DESC", self._empresa_id, de, ate)

    # -- cozinha ------------------------------------------------------------------------

    def na_cozinha(self) -> list[Linha]:
        """Itens que passam pela cozinha e ainda não foram entregues, por ordem de chegada."""
        return self._linhas(
            "SELECT i.id, i.nome, i.quantidade, i.observacao, i.status, i.lancado_em, i.atualizado_em, "
            "c.id AS comanda_id, c.numero, c.mesa, u.usuario AS garcom "
            "FROM cmd_itens i JOIN cmd_comandas c ON c.id = i.comanda_id LEFT JOIN usuarios u ON u.id = i.lancado_por "
            "WHERE i.empresa_id = ? AND i.vai_cozinha = 1 AND i.status IN ('pendente', 'preparando', 'pronto') "
            "AND c.status != 'cancelada' ORDER BY i.lancado_em, i.id", self._empresa_id)

    def entregues_desde(self, desde: str, maximo: int) -> list[Linha]:
        """Itens da cozinha entregues depois de `desde` (texto UTC), os mais novos primeiro."""
        return self._linhas(
            "SELECT i.id, i.nome, i.quantidade, i.atualizado_em, c.numero, c.mesa FROM cmd_itens i "
            "JOIN cmd_comandas c ON c.id = i.comanda_id "
            "WHERE i.empresa_id = ? AND i.vai_cozinha = 1 AND i.status = 'entregue' AND i.atualizado_em >= ? "
            "AND c.status != 'cancelada' ORDER BY i.atualizado_em DESC, i.id DESC LIMIT ?", self._empresa_id, desde, maximo)
