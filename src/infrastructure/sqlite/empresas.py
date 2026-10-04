"""Repositório das empresas no SQLite: módulos, limites e uso do plano, configurações e cadastro."""

import sqlite3
from collections.abc import Mapping

from src.domain.empresas import COLUNAS, CodigoEmUso, Limites, Uso


class RepositorioDeEmpresasSQLite:
    def __init__(self, conexao: sqlite3.Connection) -> None:
        self._c = conexao

    def existe(self, empresa_id: int) -> bool:
        return self._c.execute("SELECT 1 FROM empresas WHERE id = ?", (empresa_id,)).fetchone() is not None

    def modulos(self, empresa_id: int) -> tuple[str | None, str | None] | None:
        linha = self._c.execute(
            "SELECT e.modulos_liberados, p.modulos AS do_plano FROM empresas e "
            "LEFT JOIN planos p ON p.id = e.plano_id WHERE e.id = ?", (empresa_id,)).fetchone()
        return None if linha is None else (linha["modulos_liberados"], linha["do_plano"])

    def limites(self, empresa_id: int) -> Limites:
        linha = self._c.execute("SELECT limite_telas, limite_mb FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        return Limites(linha["limite_telas"], linha["limite_mb"]) if linha else Limites(0, 0)

    def uso(self, empresa_id: int) -> Uso:
        telas = self._c.execute("SELECT COUNT(*) FROM telas WHERE empresa_id = ?", (empresa_id,)).fetchone()[0]
        bytes_usados = self._c.execute(
            "SELECT COALESCE(SUM(tamanho), 0) FROM propagandas WHERE empresa_id = ?", (empresa_id,)).fetchone()[0]
        return Uso(int(telas), int(bytes_usados))

    def codigo_em_uso(self, codigo: str, exceto_id: int | None = None) -> bool:
        return self._c.execute("SELECT 1 FROM empresas WHERE slug = ? AND id IS NOT ?", (codigo, exceto_id)).fetchone() is not None

    def salvar_configuracoes(self, empresa_id: int, nome: str, codigo: str, emails: str, webhook: str,
                             cadastro: Mapping[str, str]) -> None:
        try:
            with self._c:
                self._c.execute("UPDATE empresas SET nome = ?, slug = ?, alerta_emails = ?, alerta_webhook = ? WHERE id = ?",
                                (nome, codigo, emails, webhook, empresa_id))
                for coluna, valor in cadastro.items():
                    if coluna not in COLUNAS:   # o nome da coluna entra no SQL: só as do cadastro
                        raise ValueError(f"coluna desconhecida: {coluna}")
                    self._c.execute(f"UPDATE empresas SET {coluna} = ? WHERE id = ?", (valor, empresa_id))
        except sqlite3.IntegrityError:   # outra loja pegou o mesmo código ao mesmo tempo
            raise CodigoEmUso(codigo) from None

    def remover_logo(self, empresa_id: int) -> None:
        with self._c:
            self._c.execute("UPDATE empresas SET logo = '' WHERE id = ?", (empresa_id,))

    def criar(self, nome: str, codigo: str, email_cobranca: str) -> int:
        try:
            with self._c:
                cursor = self._c.execute("INSERT INTO empresas (nome, slug, email_cobranca) VALUES (?, ?, ?)",
                                         (nome, codigo, email_cobranca))
        except sqlite3.IntegrityError:
            raise CodigoEmUso(codigo) from None
        return int(cursor.lastrowid or 0)

    def apagar(self, empresa_id: int) -> None:
        with self._c:
            self._c.execute("DELETE FROM empresas WHERE id = ?", (empresa_id,))
