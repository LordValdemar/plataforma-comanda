"""O que as telas do Painel mostram (só leitura), sempre de uma loja: telas, grupos e para onde vai cada propaganda."""

import sqlite3
from typing import Any

Linha = dict[str, Any]


class ConsultasDoPainel:
    def __init__(self, conexao: sqlite3.Connection, empresa_id: int) -> None:
        self._c = conexao
        self._empresa_id = empresa_id

    def _linhas(self, sql: str) -> list[Linha]:
        return [dict(linha) for linha in self._c.execute(sql, (self._empresa_id,))]

    def telas_para_escolher(self) -> list[Linha]:
        return self._linhas("SELECT id, nome FROM telas WHERE empresa_id = ? ORDER BY nome")

    def grupos_para_escolher(self) -> list[Linha]:
        return self._linhas("SELECT id, nome FROM grupos WHERE empresa_id = ? ORDER BY nome")

    def telas(self) -> list[Linha]:
        """Todas as telas, com o nome do grupo."""
        return self._linhas("SELECT t.*, gr.nome AS grupo_nome FROM telas t LEFT JOIN grupos gr ON gr.id = t.grupo_id "
                            "WHERE t.empresa_id = ? ORDER BY t.nome")

    def grupos_com_total(self) -> list[Linha]:
        """Os grupos e quantas telas cada um tem."""
        return self._linhas("SELECT gr.*, COUNT(t.id) AS total FROM grupos gr LEFT JOIN telas t ON t.grupo_id = gr.id "
                            "WHERE gr.empresa_id = ? GROUP BY gr.id ORDER BY gr.nome")

    def destinos_por_propaganda(self) -> dict[int, dict[str, Any]]:
        """Para a lista: {propaganda_id: {"telas": {ids}, "grupos": {ids}, "nomes": [..]}} (só as que escolhem telas)."""
        resultado: dict[int, dict[str, Any]] = {}
        for linha in self._c.execute(
            "SELECT d.propaganda_id, d.tela_id, d.grupo_id, t.nome AS tela_nome, gr.nome AS grupo_nome "
            "FROM propaganda_destinos d LEFT JOIN telas t ON t.id = d.tela_id LEFT JOIN grupos gr ON gr.id = d.grupo_id "
            "JOIN propagandas p ON p.id = d.propaganda_id WHERE p.empresa_id = ? ORDER BY gr.nome, t.nome",
            (self._empresa_id,),
        ):
            destino = resultado.setdefault(linha["propaganda_id"], {"telas": set(), "grupos": set(), "nomes": []})
            if linha["grupo_id"]:
                destino["grupos"].add(linha["grupo_id"])
                destino["nomes"].append("Grupo " + linha["grupo_nome"])
            else:
                destino["telas"].add(linha["tela_id"])
                destino["nomes"].append(linha["tela_nome"])
        return resultado
