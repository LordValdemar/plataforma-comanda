"""Consultas do relatório de exibições do Painel no SQLite, sempre de uma loja."""

import sqlite3
from datetime import date

from src.domain.relatorios.exibicoes import ExibicoesDaPropaganda, ExibicoesDaTela, ExibicoesDoDia


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
