"""Repositórios das telas no SQLite: o da loja (cadastro) e o da TV (conexão, sem saber a loja)."""

import sqlite3
from datetime import datetime

from src.domain.painel import PedidoDeConexao, Tela

from .datas import de_texto, para_texto


def _tela(linha: sqlite3.Row) -> Tela:
    return Tela(
        id=linha["id"], empresa_id=linha["empresa_id"], nome=linha["nome"], codigo=linha["codigo"],
        grupo_id=linha["grupo_id"], letreiro=linha["letreiro"], aparelho_hash=linha["aparelho_hash"],
        aceita_link=bool(linha["aceita_link"]), ultimo_contato=de_texto(linha["ultimo_contato"]),
        fechada_em=de_texto(linha["fechada_em"]),
    )


def _pedido(linha: sqlite3.Row) -> PedidoDeConexao:
    criado_em = de_texto(linha["criado_em"])
    assert criado_em is not None
    return PedidoDeConexao(id=linha["id"], codigo=linha["codigo"], segredo_hash=linha["segredo_hash"],
                           criado_em=criado_em, tela_id=linha["tela_id"])


class RepositorioDeTelasSQLite:
    def __init__(self, conexao: sqlite3.Connection, empresa_id: int) -> None:
        self._c = conexao
        self._empresa_id = empresa_id

    def telas(self) -> list[Tela]:
        return [_tela(linha) for linha in self._c.execute(
            "SELECT * FROM telas WHERE empresa_id = ? ORDER BY nome", (self._empresa_id,))]

    def tela(self, tela_id: int) -> Tela | None:
        linha = self._c.execute("SELECT * FROM telas WHERE id = ? AND empresa_id = ?",
                                (tela_id, self._empresa_id)).fetchone()
        return None if linha is None else _tela(linha)

    def endereco_em_uso(self, codigo: str) -> bool:
        return self._c.execute("SELECT 1 FROM telas WHERE codigo = ?", (codigo,)).fetchone() is not None

    def inserir_tela(self, nome: str, codigo: str, grupo_id: int | None) -> int:
        with self._c:
            cursor = self._c.execute(
                "INSERT INTO telas (empresa_id, nome, codigo, grupo_id, aceita_link) VALUES (?, ?, ?, ?, 0)",
                (self._empresa_id, nome, codigo, grupo_id),
            )
        return int(cursor.lastrowid or 0)

    def atualizar_tela(self, tela_id: int, nome: str, grupo_id: int | None, letreiro: str | None) -> None:
        with self._c:
            self._c.execute("UPDATE telas SET nome = ?, grupo_id = ?, letreiro = ? WHERE id = ? AND empresa_id = ?",
                            (nome, grupo_id, letreiro, tela_id, self._empresa_id))

    def trocar_endereco(self, tela_id: int, codigo: str) -> None:
        with self._c:
            self._c.execute("UPDATE telas SET codigo = ? WHERE id = ? AND empresa_id = ?", (codigo, tela_id, self._empresa_id))

    def desligar_aparelho(self, tela_id: int) -> None:
        with self._c:
            self._c.execute("UPDATE telas SET aparelho_hash = NULL, pareada_em = NULL, aceita_link = 0 "
                            "WHERE id = ? AND empresa_id = ?", (tela_id, self._empresa_id))

    def excluir_tela(self, tela_id: int) -> None:
        with self._c:
            self._c.execute("DELETE FROM telas WHERE id = ? AND empresa_id = ?", (tela_id, self._empresa_id))

    def grupo(self, grupo_id: int) -> str | None:
        linha = self._c.execute("SELECT nome FROM grupos WHERE id = ? AND empresa_id = ?",
                                (grupo_id, self._empresa_id)).fetchone()
        return None if linha is None else str(linha["nome"])

    def grupo_com_nome(self, nome: str) -> bool:
        return self._c.execute("SELECT 1 FROM grupos WHERE nome = ? AND empresa_id = ?",
                               (nome, self._empresa_id)).fetchone() is not None

    def inserir_grupo(self, nome: str) -> int:
        with self._c:
            cursor = self._c.execute("INSERT INTO grupos (empresa_id, nome) VALUES (?, ?)", (self._empresa_id, nome))
        return int(cursor.lastrowid or 0)

    def excluir_grupo(self, grupo_id: int) -> None:
        with self._c:
            self._c.execute("DELETE FROM grupos WHERE id = ? AND empresa_id = ?", (grupo_id, self._empresa_id))

    def pedido(self, codigo: str) -> PedidoDeConexao | None:
        """O pedido não é de loja nenhuma ainda: vale o código (impossível de adivinhar em 10 minutos)."""
        linha = self._c.execute("SELECT * FROM pareamentos WHERE codigo = ?", (codigo,)).fetchone()
        return None if linha is None else _pedido(linha)

    def ligar_aparelho(self, tela_id: int, pedido: PedidoDeConexao, agora: datetime) -> None:
        with self._c:
            alterou = self._c.execute(
                "UPDATE telas SET aparelho_hash = ?, pareada_em = ?, aceita_link = 0 WHERE id = ? AND empresa_id = ?",
                (pedido.segredo_hash, para_texto(agora), tela_id, self._empresa_id),
            ).rowcount
            if alterou:
                self._c.execute("UPDATE pareamentos SET tela_id = ? WHERE id = ?", (tela_id, pedido.id))


class RepositorioDeConexoesSQLite:
    def __init__(self, conexao: sqlite3.Connection) -> None:
        self._c = conexao

    def tela_por_endereco(self, codigo: str) -> Tela | None:
        linha = self._c.execute("SELECT * FROM telas WHERE codigo = ?", (codigo,)).fetchone()
        return None if linha is None else _tela(linha)

    def tela(self, tela_id: int) -> Tela | None:
        linha = self._c.execute("SELECT * FROM telas WHERE id = ?", (tela_id,)).fetchone()
        return None if linha is None else _tela(linha)

    def ligar_primeiro_aparelho(self, tela_id: int, cracha_hash: str, agora: datetime) -> bool:
        """Só o primeiro aparelho leva (UPDATE ... WHERE aparelho_hash IS NULL)."""
        with self._c:
            return bool(self._c.execute(
                "UPDATE telas SET aparelho_hash = ?, pareada_em = ?, aceita_link = 0 WHERE id = ? AND aparelho_hash IS NULL",
                (cracha_hash, para_texto(agora), tela_id),
            ).rowcount)

    def apagar_pedidos_antigos(self, antes_de: datetime) -> None:
        with self._c:
            self._c.execute("DELETE FROM pareamentos WHERE criado_em < ?", (para_texto(antes_de),))

    def pedidos_abertos(self) -> int:
        return int(self._c.execute("SELECT COUNT(*) FROM pareamentos").fetchone()[0])

    def codigo_de_pedido_em_uso(self, codigo: str) -> bool:
        return self._c.execute("SELECT 1 FROM pareamentos WHERE codigo = ?", (codigo,)).fetchone() is not None

    def inserir_pedido(self, codigo: str, segredo_hash: str, agora: datetime) -> None:
        with self._c:
            self._c.execute("INSERT INTO pareamentos (codigo, segredo_hash, criado_em) VALUES (?, ?, ?)",
                            (codigo, segredo_hash, para_texto(agora)))

    def pedido_pelo_segredo(self, segredo_hash: str) -> PedidoDeConexao | None:
        linha = self._c.execute("SELECT * FROM pareamentos WHERE segredo_hash = ?", (segredo_hash,)).fetchone()
        return None if linha is None else _pedido(linha)

    def apagar_pedido(self, pedido_id: int) -> None:
        with self._c:
            self._c.execute("DELETE FROM pareamentos WHERE id = ?", (pedido_id,))

    def registrar_contato(self, tela_id: int, agora: datetime, ip: str, navegador: str,
                          exibindo: str | None, mudar_exibindo: bool) -> None:
        """Deu sinal de vida: a janela está aberta (fechada_em volta a vazio)."""
        campos = "ultimo_contato = ?, ultimo_ip = ?, navegador = ?, fechada_em = NULL"
        valores: list[object] = [para_texto(agora), ip, navegador]
        if mudar_exibindo:
            campos += ", exibindo = ?"
            valores.append(exibindo)
        with self._c:
            self._c.execute(f"UPDATE telas SET {campos} WHERE id = ?", (*valores, tela_id))

    def marcar_fechada(self, tela_id: int, agora: datetime) -> None:
        with self._c:
            self._c.execute("UPDATE telas SET ultimo_contato = ?, fechada_em = ?, exibindo = NULL WHERE id = ?",
                            (para_texto(agora), para_texto(agora), tela_id))
