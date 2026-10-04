"""Repositório das propagandas no SQLite (propagandas, propaganda_destinos, exibicoes), restrito a uma loja."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from src.domain.painel import Destinos, Exibicao, Programacao, Propaganda

from .datas import para_texto

_CAMPOS = {"ativo", "duracao", "letreiro", "para_todas"}   # os que se mudam em lote


def _propaganda(linha: sqlite3.Row, destinos: Destinos) -> Propaganda:
    return Propaganda(
        id=linha["id"], nome=linha["nome"], arquivo=linha["arquivo"], tipo=linha["tipo"], duracao=linha["duracao"],
        posicao=linha["posicao"], tamanho=linha["tamanho"], ativo=bool(linha["ativo"]), inicio=linha["inicio"],
        fim=linha["fim"], dias_semana=linha["dias_semana"], hora_inicio=linha["hora_inicio"],
        hora_fim=linha["hora_fim"], para_todas=bool(linha["para_todas"]), letreiro=linha["letreiro"],
        destinos=destinos,
    )


class RepositorioDePropagandasSQLite:
    def __init__(self, conexao: sqlite3.Connection, empresa_id: int) -> None:
        self._c = conexao
        self._empresa_id = empresa_id
        self._na_transacao = False

    @contextmanager
    def transacao(self) -> Iterator[None]:
        if self._na_transacao:
            yield
            return
        with self._c:
            self._c.execute("BEGIN IMMEDIATE")
            self._na_transacao = True
            try:
                yield
            finally:
                self._na_transacao = False

    @contextmanager
    def _gravando(self) -> Iterator[None]:
        if self._na_transacao:
            yield
        else:
            with self._c:
                yield

    # -- leitura -------------------------------------------------------------------------

    def _destinos(self, propaganda_id: int | None = None) -> dict[int, Destinos]:
        filtro, parametros = ("AND d.propaganda_id = ?", (self._empresa_id, propaganda_id)) if propaganda_id \
            else ("", (self._empresa_id,))
        telas: dict[int, set[int]] = {}
        grupos: dict[int, set[int]] = {}
        for linha in self._c.execute(
            "SELECT d.propaganda_id, d.tela_id, d.grupo_id FROM propaganda_destinos d "
            f"JOIN propagandas p ON p.id = d.propaganda_id WHERE p.empresa_id = ? {filtro}", parametros,
        ):
            if linha["grupo_id"]:
                grupos.setdefault(linha["propaganda_id"], set()).add(linha["grupo_id"])
            else:
                telas.setdefault(linha["propaganda_id"], set()).add(linha["tela_id"])
        return {pid: Destinos(frozenset(telas.get(pid, ())), frozenset(grupos.get(pid, ())))
                for pid in telas.keys() | grupos.keys()}

    def listar(self) -> list[Propaganda]:
        destinos = self._destinos()
        return [_propaganda(linha, destinos.get(linha["id"], Destinos())) for linha in self._c.execute(
            "SELECT * FROM propagandas WHERE empresa_id = ? ORDER BY posicao, id", (self._empresa_id,)
        )]

    def buscar(self, propaganda_id: int) -> Propaganda | None:
        linha = self._c.execute("SELECT * FROM propagandas WHERE id = ? AND empresa_id = ?",
                                (propaganda_id, self._empresa_id)).fetchone()
        if linha is None:
            return None
        return _propaganda(linha, self._destinos(propaganda_id).get(propaganda_id, Destinos()))

    def telas_da_loja(self) -> set[int]:
        return {r["id"] for r in self._c.execute("SELECT id FROM telas WHERE empresa_id = ?", (self._empresa_id,))}

    def grupos_da_loja(self) -> set[int]:
        return {r["id"] for r in self._c.execute("SELECT id FROM grupos WHERE empresa_id = ?", (self._empresa_id,))}

    def nomes(self) -> dict[int, str]:
        return {r["id"]: r["nome"] for r in self._c.execute(
            "SELECT id, nome FROM propagandas WHERE empresa_id = ?", (self._empresa_id,))}

    # -- gravação --------------------------------------------------------------------------

    def inserir(self, nome: str, arquivo: str, tipo: str, tamanho: int, duracao: int, para_todas: bool) -> int:
        with self._gravando():
            cursor = self._c.execute(
                "INSERT INTO propagandas (empresa_id, nome, arquivo, tipo, tamanho, duracao, para_todas, posicao) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(posicao), 0) + 1 FROM propagandas WHERE empresa_id = ?))",
                (self._empresa_id, nome, arquivo, tipo, tamanho, duracao, int(para_todas), self._empresa_id),
            )
        return int(cursor.lastrowid or 0)

    def gravar_programacao(self, propaganda_id: int, programacao: Programacao) -> None:
        p = programacao
        with self._gravando():
            self._c.execute(
                "UPDATE propagandas SET nome = ?, duracao = ?, ativo = ?, inicio = ?, fim = ?, dias_semana = ?, "
                "hora_inicio = ?, hora_fim = ?, para_todas = ?, letreiro = ? WHERE id = ? AND empresa_id = ?",
                (p.nome, p.duracao, int(p.ativo), p.inicio, p.fim, p.dias_semana, p.hora_inicio, p.hora_fim,
                 int(p.para_todas), p.letreiro, propaganda_id, self._empresa_id),
            )

    def gravar_destinos(self, propaganda_id: int, destinos: Destinos) -> None:
        with self._gravando():
            if self._c.execute("SELECT 1 FROM propagandas WHERE id = ? AND empresa_id = ?",
                               (propaganda_id, self._empresa_id)).fetchone() is None:
                return  # propaganda de outra loja: nada muda
            self._c.execute("DELETE FROM propaganda_destinos WHERE propaganda_id = ?", (propaganda_id,))
            self._c.executemany("INSERT INTO propaganda_destinos (propaganda_id, tela_id) VALUES (?, ?)",
                                [(propaganda_id, t) for t in sorted(destinos.telas)])
            self._c.executemany("INSERT INTO propaganda_destinos (propaganda_id, grupo_id) VALUES (?, ?)",
                                [(propaganda_id, gr) for gr in sorted(destinos.grupos)])

    def gravar_ordem(self, ids: list[int]) -> None:
        with self._gravando():
            self._c.executemany("UPDATE propagandas SET posicao = ? WHERE id = ? AND empresa_id = ?",
                                [(numero, item_id, self._empresa_id) for numero, item_id in enumerate(ids, start=1)])

    def definir(self, ids: Iterable[int], campo: str, valor: object) -> None:
        if campo not in _CAMPOS:
            raise ValueError(campo)
        lista = list(ids)
        if isinstance(valor, bool):
            valor = int(valor)
        marcas = ",".join("?" * len(lista))
        with self._gravando():
            self._c.execute(f"UPDATE propagandas SET {campo} = ? WHERE empresa_id = ? AND id IN ({marcas})",
                            (valor, self._empresa_id, *lista))

    def excluir(self, propaganda_id: int) -> None:
        with self._gravando():
            self._c.execute("DELETE FROM propagandas WHERE id = ? AND empresa_id = ?", (propaganda_id, self._empresa_id))

    def config(self, chave: str) -> str | None:
        linha = self._c.execute("SELECT valor FROM configuracoes WHERE empresa_id = ? AND chave = ?",
                                (self._empresa_id, chave)).fetchone()
        return None if linha is None else str(linha["valor"])

    def gravar_config(self, chave: str, valor: str) -> None:
        with self._gravando():
            self._c.execute(
                "INSERT INTO configuracoes (empresa_id, chave, valor) VALUES (?, ?, ?) "
                "ON CONFLICT(empresa_id, chave) DO UPDATE SET valor = excluded.valor",
                (self._empresa_id, chave, valor),
            )

    def gravar_exibicoes(self, tela_id: int, exibicoes: list[tuple[Exibicao, str]]) -> int:
        """OR IGNORE: se a TV reenviar o mesmo registro (queda de rede), não duplica."""
        with self._gravando():
            cursor = self._c.executemany(
                "INSERT OR IGNORE INTO exibicoes (empresa_id, tela_id, propaganda_id, propaganda_nome, exibido_em, duracao) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(self._empresa_id, tela_id, e.propaganda_id, nome, para_texto(e.inicio), e.duracao) for e, nome in exibicoes],
            )
        return int(cursor.rowcount)
