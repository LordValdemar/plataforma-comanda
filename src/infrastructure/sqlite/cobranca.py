"""Repositório da cobrança no SQLite (planos, dados de cobrança das empresas, faturas e eventos do webhook).

A cobrança é da plataforma: este repositório enxerga todas as empresas (não é restrito a uma loja).
"""

import sqlite3
from datetime import date, datetime

from src.domain.cobranca import EM_ABERTO, DadosDoPlano, EmpresaCobrada, Fatura, Plano

from .datas import para_texto


def _empresa(linha: sqlite3.Row) -> EmpresaCobrada:
    return EmpresaCobrada(
        id=linha["id"], nome=linha["nome"], ativa=bool(linha["ativa"]), motivo_suspensao=linha["motivo_suspensao"],
        cobranca_automatica=bool(linha["cobranca_automatica"]), plano_id=linha["plano_id"],
        documento=linha["documento"] or "", email_cobranca=linha["email_cobranca"] or "",
        asaas_cliente_id=linha["asaas_cliente_id"], asaas_assinatura_id=linha["asaas_assinatura_id"],
    )


def _plano(linha: sqlite3.Row) -> Plano:
    return Plano(id=linha["id"], nome=linha["nome"], preco_centavos=linha["preco_centavos"],
                 limite_telas=linha["limite_telas"], limite_mb=linha["limite_mb"], modulos=linha["modulos"],
                 descricao=linha["descricao"], ativo=bool(linha["ativo"]))


class RepositorioDeCobrancaSQLite:
    def __init__(self, conexao: sqlite3.Connection) -> None:
        self._c = conexao

    # -- leitura ------------------------------------------------------------------------------

    def empresa(self, empresa_id: int) -> EmpresaCobrada | None:
        linha = self._c.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        return None if linha is None else _empresa(linha)

    def empresa_da_assinatura(self, assinatura_id: str) -> EmpresaCobrada | None:
        linha = self._c.execute("SELECT * FROM empresas WHERE asaas_assinatura_id = ?", (assinatura_id,)).fetchone()
        return None if linha is None else _empresa(linha)

    def empresas_com_assinatura(self) -> list[EmpresaCobrada]:
        return [_empresa(linha) for linha in self._c.execute("SELECT * FROM empresas WHERE asaas_assinatura_id IS NOT NULL")]

    def empresas_com_cobranca_automatica(self) -> list[int]:
        return [int(linha["id"]) for linha in self._c.execute("SELECT id FROM empresas WHERE cobranca_automatica = 1")]

    def plano(self, plano_id: int) -> Plano | None:
        linha = self._c.execute("SELECT * FROM planos WHERE id = ?", (plano_id,)).fetchone()
        return None if linha is None else _plano(linha)

    def plano_com_nome(self, nome: str) -> bool:
        return self._c.execute("SELECT 1 FROM planos WHERE nome = ?", (nome,)).fetchone() is not None

    def tem_fatura_vencida_antes_de(self, empresa_id: int, dia: date) -> bool:
        return self._c.execute(
            "SELECT 1 FROM faturas WHERE empresa_id = ? AND status = 'OVERDUE' AND vencimento < ?",
            (empresa_id, dia.isoformat()),
        ).fetchone() is not None

    def faturas_em_aberto(self, empresa_id: int) -> list[str]:
        situacoes = sorted(EM_ABERTO)
        return [str(linha["asaas_id"]) for linha in self._c.execute(
            f"SELECT asaas_id FROM faturas WHERE empresa_id = ? AND status IN ({','.join('?' * len(situacoes))})",
            (empresa_id, *situacoes),
        )]

    def evento_processado(self, evento_id: str) -> bool:
        return self._c.execute("SELECT 1 FROM webhook_eventos WHERE id = ?", (evento_id,)).fetchone() is not None

    # -- gravação ---------------------------------------------------------------------------------

    def _gravar(self, sql: str, parametros: tuple[object, ...]) -> int:
        with self._c:
            return int(self._c.execute(sql, parametros).lastrowid or 0)

    def inserir_plano(self, dados: DadosDoPlano) -> int:
        return self._gravar(
            "INSERT INTO planos (nome, preco_centavos, limite_telas, limite_mb, modulos, descricao) VALUES (?, ?, ?, ?, ?, ?)",
            (dados.nome, dados.preco_centavos, dados.limite_telas, dados.limite_mb, dados.modulos, dados.descricao),
        )

    def atualizar_plano(self, plano_id: int, dados: DadosDoPlano) -> None:
        with self._c:
            self._c.execute(
                "UPDATE planos SET nome = ?, preco_centavos = ?, limite_telas = ?, limite_mb = ?, ativo = ?, modulos = ?, "
                "descricao = ? WHERE id = ?",
                (dados.nome, dados.preco_centavos, dados.limite_telas, dados.limite_mb, int(dados.ativo), dados.modulos,
                 dados.descricao, plano_id),
            )
            self._c.execute("UPDATE empresas SET limite_telas = ?, limite_mb = ? WHERE plano_id = ?",
                            (dados.limite_telas, dados.limite_mb, plano_id))

    def gravar_dados_de_cobranca(self, empresa_id: int, plano: Plano | None, documento: str, email: str,
                                 automatica: bool) -> None:
        with self._c:
            self._c.execute(
                "UPDATE empresas SET plano_id = ?, documento = ?, email_cobranca = ?, cobranca_automatica = ? WHERE id = ?",
                (plano.id if plano else None, documento, email, int(automatica), empresa_id),
            )
            if plano is not None:
                self._c.execute("UPDATE empresas SET limite_telas = ?, limite_mb = ? WHERE id = ?",
                                (plano.limite_telas, plano.limite_mb, empresa_id))

    def gravar_contato(self, empresa_id: int, documento: str, email: str) -> None:
        self._gravar("UPDATE empresas SET documento = ?, email_cobranca = ? WHERE id = ?", (documento, email, empresa_id))

    def gravar_cliente_no_gateway(self, empresa_id: int, cliente_id: str) -> None:
        self._gravar("UPDATE empresas SET asaas_cliente_id = ? WHERE id = ?", (cliente_id, empresa_id))

    def gravar_assinatura(self, empresa_id: int, assinatura_id: str | None, automatica: bool) -> None:
        self._gravar("UPDATE empresas SET asaas_assinatura_id = ?, cobranca_automatica = ? WHERE id = ?",
                     (assinatura_id, int(automatica), empresa_id))

    def gravar_plano_da_empresa(self, empresa_id: int, plano: Plano) -> None:
        self._gravar(
            "UPDATE empresas SET plano_id = ?, cobranca_automatica = 1, limite_telas = ?, limite_mb = ? WHERE id = ?",
            (plano.id, plano.limite_telas, plano.limite_mb, empresa_id),
        )

    def tirar_plano(self, empresa_id: int) -> None:
        self._gravar("UPDATE empresas SET plano_id = NULL, asaas_assinatura_id = NULL, cobranca_automatica = 0 WHERE id = ?",
                     (empresa_id,))

    def suspender_por_inadimplencia(self, empresa_id: int) -> None:
        self._gravar("UPDATE empresas SET ativa = 0, motivo_suspensao = 'inadimplencia' WHERE id = ?", (empresa_id,))

    def reativar(self, empresa_id: int) -> None:
        self._gravar("UPDATE empresas SET ativa = 1, motivo_suspensao = NULL WHERE id = ?", (empresa_id,))

    def gravar_fatura(self, empresa_id: int, fatura: Fatura, agora: datetime) -> None:
        """Cria ou atualiza pela id do Asaas (o mesmo aviso aplicado duas vezes dá no mesmo)."""
        self._gravar(
            """
            INSERT INTO faturas (empresa_id, asaas_id, valor_centavos, vencimento, status, link, pago_em, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asaas_id) DO UPDATE SET
                valor_centavos = excluded.valor_centavos, vencimento = excluded.vencimento,
                status = excluded.status, link = COALESCE(excluded.link, faturas.link),
                pago_em = excluded.pago_em, atualizado_em = excluded.atualizado_em
            """,
            (empresa_id, fatura.asaas_id, fatura.valor_centavos, fatura.vencimento, fatura.status, fatura.link,
             fatura.pago_em, para_texto(agora)),
        )

    def cancelar_faturas(self, asaas_ids: list[str]) -> None:
        with self._c:
            self._c.executemany("UPDATE faturas SET status = 'DELETED' WHERE asaas_id = ?", [(i,) for i in asaas_ids])

    def marcar_evento(self, evento_id: str, agora: datetime) -> None:
        self._gravar("INSERT OR IGNORE INTO webhook_eventos (id, recebido_em) VALUES (?, ?)", (evento_id, para_texto(agora)))
