"""Repositório das contas de usuário no SQLite (usuarios, com a situação da empresa de cada um)."""

import sqlite3

from src.domain.contas import Conta

_SELECT = ("SELECT u.*, e.ativa AS empresa_ativa, e.motivo_suspensao FROM usuarios u "
           "JOIN empresas e ON e.id = u.empresa_id ")


def _conta(linha: sqlite3.Row) -> Conta:
    return Conta(
        id=linha["id"], empresa_id=linha["empresa_id"], usuario=linha["usuario"], papel=linha["papel"],
        senha_hash=linha["senha_hash"], token_sessao=linha["token_sessao"], plataforma=bool(linha["plataforma"]),
        fecha_conta=bool(linha["fecha_conta"]), totp_segredo=linha["totp_segredo"], totp_pendente=linha["totp_pendente"],
        totp_ultimo=int(linha["totp_ultimo"] or 0), empresa_ativa=bool(linha["empresa_ativa"]),
        motivo_suspensao=linha["motivo_suspensao"],
    )


class RepositorioDeContasSQLite:
    def __init__(self, conexao: sqlite3.Connection) -> None:
        self._c = conexao

    def _gravar(self, sql: str, parametros: tuple[object, ...]) -> int:
        with self._c:
            return int(self._c.execute(sql, parametros).lastrowid or 0)

    # -- leitura ------------------------------------------------------------------------------

    def conta(self, usuario_id: int) -> Conta | None:
        linha = self._c.execute(_SELECT + "WHERE u.id = ?", (usuario_id,)).fetchone()
        return None if linha is None else _conta(linha)

    def contas_com_nome(self, nome: str, loja: str | None) -> list[Conta]:
        if loja:
            linhas = self._c.execute(_SELECT + "WHERE u.usuario = ? AND e.slug = ?", (nome, loja.strip().lower()))
        else:
            linhas = self._c.execute(_SELECT + "WHERE u.usuario = ?", (nome,))
        return [_conta(linha) for linha in linhas]

    def existe_alguem(self) -> bool:
        return self._c.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone() is not None

    def empresa_existe(self, empresa_id: int) -> bool:
        return self._c.execute("SELECT 1 FROM empresas WHERE id = ?", (empresa_id,)).fetchone() is not None

    def nome_em_uso(self, empresa_id: int, nome: str) -> bool:
        return self._c.execute("SELECT 1 FROM usuarios WHERE empresa_id = ? AND usuario = ?",
                               (empresa_id, nome)).fetchone() is not None

    # -- gravação -------------------------------------------------------------------------------

    def inserir(self, empresa_id: int, nome: str, senha_hash: str, papel: str, plataforma: bool, token: str) -> int:
        return self._gravar(
            "INSERT INTO usuarios (empresa_id, usuario, senha_hash, papel, plataforma, token_sessao) VALUES (?, ?, ?, ?, ?, ?)",
            (empresa_id, nome, senha_hash, papel, int(plataforma), token),
        )

    def gravar_senha(self, usuario_id: int, senha_hash: str, token: str) -> None:
        self._gravar("UPDATE usuarios SET senha_hash = ?, token_sessao = ? WHERE id = ?", (senha_hash, token, usuario_id))

    def gravar_totp_pendente(self, usuario_id: int, segredo: str) -> None:
        self._gravar("UPDATE usuarios SET totp_pendente = ? WHERE id = ?", (segredo, usuario_id))

    def ativar_totp(self, usuario_id: int, segredo: str, contador: int, token: str) -> None:
        self._gravar("UPDATE usuarios SET totp_segredo = ?, totp_pendente = NULL, totp_ultimo = ?, token_sessao = ? "
                     "WHERE id = ?", (segredo, contador, token, usuario_id))

    def desativar_totp(self, usuario_id: int, token: str) -> None:
        self._gravar("UPDATE usuarios SET totp_segredo = NULL, totp_ultimo = 0, token_sessao = ? WHERE id = ?",
                     (token, usuario_id))

    def gravar_totp_ultimo(self, usuario_id: int, contador: int) -> None:
        self._gravar("UPDATE usuarios SET totp_ultimo = ? WHERE id = ?", (contador, usuario_id))

    def mudar_papel(self, usuario_id: int, papel: str) -> None:
        """"Fecha contas" é uma permissão extra do garçom: não acompanha a pessoa para outro papel."""
        self._gravar("UPDATE usuarios SET papel = ?, fecha_conta = CASE WHEN ? = 'garcom' THEN fecha_conta ELSE 0 END "
                     "WHERE id = ?", (papel, papel, usuario_id))

    def definir_fecha_conta(self, usuario_id: int, pode: bool) -> None:
        self._gravar("UPDATE usuarios SET fecha_conta = ? WHERE id = ?", (int(pode), usuario_id))

    def excluir(self, usuario_id: int) -> None:
        self._gravar("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
