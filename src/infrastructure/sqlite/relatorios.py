"""Consultas dos relatórios no SQLite (vendas da Comanda e exibições do Painel), sempre de uma loja."""

import sqlite3
from datetime import date

from src.domain.relatorios.exibicoes import ExibicoesDaPropaganda, ExibicoesDaTela, ExibicoesDoDia
from src.domain.relatorios.vendas import (
    ComandaEncerrada,
    ItemCancelado,
    TaxaDaComanda,
    VendaPorForma,
    VendaPorGarcom,
    VendaPorProduto,
)

_FECHADAS = "c.empresa_id = ? AND c.status = 'fechada' AND c.fechada_em >= ? AND c.fechada_em < ?"


class RepositorioDeVendasSQLite:
    def __init__(self, conexao: sqlite3.Connection, empresa_id: int) -> None:
        self._c = conexao
        self._empresa_id = empresa_id

    def _consulta(self, sql: str, de: str, ate: str) -> list[sqlite3.Row]:
        return self._c.execute(sql, (self._empresa_id, de, ate)).fetchall()

    def totais(self, de: str, ate: str) -> tuple[int, int, int]:
        linha = self._consulta(
            "SELECT COUNT(*), COALESCE(SUM(c.total_centavos), 0), COALESCE(SUM(c.desconto_centavos), 0) "
            f"FROM cmd_comandas c WHERE {_FECHADAS}", de, ate)[0]
        return int(linha[0]), int(linha[1]), int(linha[2])

    def taxas(self, de: str, ate: str) -> list[TaxaDaComanda]:
        return [TaxaDaComanda(linha["taxa_centavos"], bool(linha["cobrar_taxa"]), linha["taxa_percentual"], int(linha["sub"]))
                for linha in self._consulta(
                    "SELECT c.taxa_centavos, c.cobrar_taxa, c.taxa_percentual, COALESCE(s.sub, 0) AS sub FROM cmd_comandas c "
                    "LEFT JOIN (SELECT comanda_id, SUM(preco_centavos * quantidade) AS sub FROM cmd_itens "
                    f"WHERE status != 'cancelado' GROUP BY comanda_id) s ON s.comanda_id = c.id WHERE {_FECHADAS}", de, ate)]

    def por_forma(self, de: str, ate: str) -> list[VendaPorForma]:
        return [VendaPorForma(linha["forma"], linha["quantidade"], linha["valor"]) for linha in self._consulta(
            "SELECT p.forma, COUNT(*) AS quantidade, SUM(p.valor_centavos) AS valor FROM cmd_pagamentos p "
            f"JOIN cmd_comandas c ON c.id = p.comanda_id WHERE {_FECHADAS} GROUP BY p.forma ORDER BY valor DESC", de, ate)]

    def por_produto(self, de: str, ate: str) -> list[VendaPorProduto]:
        return [VendaPorProduto(linha["nome"], linha["quantidade"], linha["valor"]) for linha in self._consulta(
            "SELECT i.nome, SUM(i.quantidade) AS quantidade, SUM(i.quantidade * i.preco_centavos) AS valor FROM cmd_itens i "
            f"JOIN cmd_comandas c ON c.id = i.comanda_id WHERE {_FECHADAS} AND i.status != 'cancelado' "
            "GROUP BY i.nome ORDER BY quantidade DESC, valor DESC", de, ate)]

    def por_garcom(self, de: str, ate: str) -> list[VendaPorGarcom]:
        return [VendaPorGarcom(linha["garcom"], linha["valor"]) for linha in self._consulta(
            "SELECT COALESCE(u.usuario, '—') AS garcom, SUM(i.quantidade * i.preco_centavos) AS valor FROM cmd_itens i "
            "JOIN cmd_comandas c ON c.id = i.comanda_id LEFT JOIN usuarios u ON u.id = i.lancado_por "
            f"WHERE {_FECHADAS} AND i.status != 'cancelado' GROUP BY u.usuario ORDER BY valor DESC", de, ate)]

    def cancelados(self, de: str, ate: str) -> list[ItemCancelado]:
        return [ItemCancelado(linha["comanda_id"], linha["numero"], linha["nome"], linha["quantidade"],
                              linha["motivo_cancelamento"], linha["cancelado_por_nome"], linha["atualizado_em"])
                for linha in self._consulta(
                    "SELECT i.*, c.numero, u.usuario AS cancelado_por_nome FROM cmd_itens i "
                    "JOIN cmd_comandas c ON c.id = i.comanda_id LEFT JOIN usuarios u ON u.id = i.cancelado_por "
                    "WHERE i.empresa_id = ? AND i.status = 'cancelado' AND i.atualizado_em >= ? AND i.atualizado_em < ? "
                    "ORDER BY i.atualizado_em", de, ate)]

    def abertas(self) -> int:
        return int(self._c.execute("SELECT COUNT(*) FROM cmd_comandas WHERE empresa_id = ? AND status = 'aberta'",
                                   (self._empresa_id,)).fetchone()[0])

    def encerradas(self, de: str, ate: str) -> list[ComandaEncerrada]:
        resultado = []
        for c in self._consulta(
            "SELECT c.*, GROUP_CONCAT(p.forma || ':' || p.valor_centavos, ' ') AS pagamentos, "
            "(SELECT usuario FROM usuarios WHERE id = c.garcom_id) AS garcom_nome FROM cmd_comandas c "
            "LEFT JOIN cmd_pagamentos p ON p.comanda_id = c.id "
            "WHERE c.empresa_id = ? AND c.status != 'aberta' AND c.fechada_em >= ? AND c.fechada_em < ? "
            "GROUP BY c.id ORDER BY c.fechada_em", de, ate,
        ):
            pagamentos = [(forma, int(valor)) for forma, _, valor in (p.partition(":") for p in (c["pagamentos"] or "").split())]
            resultado.append(ComandaEncerrada(
                numero=c["numero"], mesa=c["mesa"], cliente=c["cliente"], garcom=c["garcom_nome"], status=c["status"],
                aberta_em=c["aberta_em"], fechada_em=c["fechada_em"], desconto=c["desconto_centavos"],
                total=c["total_centavos"], pagamentos=pagamentos, motivo_cancelamento=c["motivo_cancelamento"],
            ))
        return resultado


class RepositorioDeExibicoesSQLite:
    def __init__(self, conexao: sqlite3.Connection, empresa_id: int) -> None:
        self._c = conexao
        self._empresa_id = empresa_id

    def _filtro(self, de: str, ate: str, tela_id: int | None) -> tuple[str, list[object]]:
        condicoes = "e.empresa_id = ? AND e.exibido_em >= ? AND e.exibido_em < ?"
        parametros: list[object] = [self._empresa_id, de, ate]
        if tela_id is not None:
            condicoes += " AND e.tela_id = ?"
            parametros.append(tela_id)
        return condicoes, parametros

    def por_propaganda(self, de: str, ate: str, tela_id: int | None) -> list[ExibicoesDaPropaganda]:
        onde, parametros = self._filtro(de, ate, tela_id)
        return [ExibicoesDaPropaganda(linha["propaganda_id"], linha["nome"], bool(linha["excluida"]), linha["exibicoes"],
                                      linha["tempo"] or 0.0, linha["telas"])
                for linha in self._c.execute(
                    "SELECT e.propaganda_id, COALESCE(p.nome, MAX(e.propaganda_nome)) AS nome, p.id IS NULL AS excluida, "
                    "COUNT(*) AS exibicoes, SUM(e.duracao) AS tempo, COUNT(DISTINCT e.tela_id) AS telas "
                    f"FROM exibicoes e LEFT JOIN propagandas p ON p.id = e.propaganda_id WHERE {onde} "
                    "GROUP BY e.propaganda_id ORDER BY exibicoes DESC", parametros)]

    def por_tela(self, de: str, ate: str, tela_id: int | None) -> list[ExibicoesDaTela]:
        onde, parametros = self._filtro(de, ate, tela_id)
        return [ExibicoesDaTela(linha["nome"], linha["exibicoes"], linha["tempo"] or 0.0) for linha in self._c.execute(
            "SELECT COALESCE(t.nome, '(tela excluída)') AS nome, COUNT(*) AS exibicoes, SUM(e.duracao) AS tempo "
            f"FROM exibicoes e LEFT JOIN telas t ON t.id = e.tela_id WHERE {onde} GROUP BY e.tela_id ORDER BY exibicoes DESC",
            parametros)]

    def por_dia(self, de: str, ate: str, tela_id: int | None, ajuste_minutos: int) -> list[ExibicoesDoDia]:
        onde, parametros = self._filtro(de, ate, tela_id)
        return [ExibicoesDoDia(date.fromisoformat(linha["dia"]), linha["tela"], linha["propaganda"], linha["exibicoes"],
                               linha["tempo"] or 0.0)
                for linha in self._c.execute(
                    "SELECT date(e.exibido_em, ?) AS dia, COALESCE(t.nome, '(tela excluída)') AS tela, "
                    "COALESCE(p.nome, MAX(e.propaganda_nome)) AS propaganda, COUNT(*) AS exibicoes, SUM(e.duracao) AS tempo "
                    "FROM exibicoes e LEFT JOIN telas t ON t.id = e.tela_id LEFT JOIN propagandas p ON p.id = e.propaganda_id "
                    f"WHERE {onde} GROUP BY dia, e.tela_id, e.propaganda_id ORDER BY dia, tela, propaganda",
                    [f"{ajuste_minutos:+d} minutes", *parametros])]
