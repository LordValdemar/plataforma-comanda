"""O que as telas e a tarefa de fundo do ponto leem (só leitura)."""

import sqlite3
from typing import Any

Linha = dict[str, Any]


class ConsultasDoPonto:
    def __init__(self, conexao: sqlite3.Connection) -> None:
        self._c = conexao

    def lojas_com_ponto_aberto(self) -> list[int]:
        """Para a tarefa de fundo: as lojas com alguém trabalhando agora."""
        return [int(linha[0]) for linha in self._c.execute("SELECT DISTINCT empresa_id FROM ponto_registros WHERE saida IS NULL")]

    def loja_do_quiosque(self, codigo: str) -> int | None:
        """A loja dona do endereço do quiosque (o código é secreto e único)."""
        linha = self._c.execute("SELECT empresa_id FROM configuracoes WHERE chave = 'ponto_quiosque' AND valor = ?",
                                (codigo,)).fetchone()
        return None if linha is None else int(linha["empresa_id"])

    def nome_da_loja(self, empresa_id: int) -> str:
        """A razão social, se preenchida; senão o nome."""
        linha = self._c.execute("SELECT COALESCE(NULLIF(razao_social, ''), nome) AS nome FROM empresas WHERE id = ?",
                                (empresa_id,)).fetchone()
        return "" if linha is None else str(linha["nome"])

    def codigo_da_loja(self, empresa_id: int) -> str:
        linha = self._c.execute("SELECT slug FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        return "" if linha is None else str(linha["slug"] or "")

    def equipe(self, empresa_id: int) -> list[Linha]:
        """As pessoas da loja e desde quando estão trabalhando (se estão); o administrador por último."""
        return [dict(linha) for linha in self._c.execute(
            "SELECT u.*, p.entrada AS trabalhando_desde FROM usuarios u "
            "LEFT JOIN ponto_registros p ON p.usuario_id = u.id AND p.saida IS NULL "
            "WHERE u.empresa_id = ? ORDER BY u.papel = 'admin', u.usuario", (empresa_id,))]
