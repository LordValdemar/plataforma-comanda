"""O que as telas da conta, da empresa, da cobrança e da plataforma leem (só leitura)."""

import sqlite3
from collections.abc import Iterator
from typing import Any

Linha = dict[str, Any]


class ConsultasDeEmpresas:
    def __init__(self, conexao: sqlite3.Connection) -> None:
        self._c = conexao

    def _linhas(self, sql: str, *parametros: object) -> list[Linha]:
        return [dict(linha) for linha in self._c.execute(sql, parametros)]

    def _linha(self, sql: str, *parametros: object) -> Linha | None:
        linha = self._c.execute(sql, parametros).fetchone()
        return None if linha is None else dict(linha)

    # -- empresa e pessoas --------------------------------------------------------------

    def ficha(self, empresa_id: int) -> Linha | None:
        """A empresa inteira (para as telas)."""
        return self._linha("SELECT * FROM empresas WHERE id = ?", empresa_id)

    def ativa(self, empresa_id: int) -> bool:
        linha = self._c.execute("SELECT ativa FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        return bool(linha and linha["ativa"])

    def loja_existe(self, codigo: str) -> bool:
        return self._c.execute("SELECT 1 FROM empresas WHERE slug = ?", (codigo.lower(),)).fetchone() is not None

    def usuario_com_loja(self, usuario_id: int) -> Linha | None:
        """A pessoa logada com o nome, o código e a situação da loja dela."""
        return self._linha(
            "SELECT u.*, COALESCE(NULLIF(e.razao_social, ''), e.nome) AS empresa_nome, e.slug AS empresa_slug, "
            "e.ativa AS empresa_ativa, e.motivo_suspensao FROM usuarios u JOIN empresas e ON e.id = u.empresa_id "
            "WHERE u.id = ?", usuario_id)

    def usuarios_da_loja(self, empresa_id: int) -> list[Linha]:
        return self._linhas("SELECT * FROM usuarios WHERE empresa_id = ? ORDER BY usuario", empresa_id)

    # -- planos e faturas ---------------------------------------------------------------

    def planos(self) -> list[Linha]:
        return self._linhas("SELECT * FROM planos ORDER BY preco_centavos")

    def plano(self, plano_id: int | None) -> Linha | None:
        return None if plano_id is None else self._linha("SELECT * FROM planos WHERE id = ?", plano_id)

    def planos_a_venda(self) -> list[Linha]:
        """Os que o cliente pode assinar: ativos e pagos, do mais barato ao mais caro."""
        return self._linhas("SELECT * FROM planos WHERE ativo = 1 AND preco_centavos > 0 ORDER BY preco_centavos, nome")

    def plano_a_venda(self, plano_id: int) -> Linha | None:
        return self._linha("SELECT * FROM planos WHERE id = ? AND ativo = 1 AND preco_centavos > 0", plano_id)

    def faturas(self, empresa_id: int, limite: int = 12) -> list[Linha]:
        return self._linhas("SELECT * FROM faturas WHERE empresa_id = ? AND status != 'DELETED' "
                            "ORDER BY vencimento DESC LIMIT ?", empresa_id, limite)

    def tem_faturas(self, empresa_id: int) -> bool:
        return self._c.execute("SELECT 1 FROM faturas WHERE empresa_id = ?", (empresa_id,)).fetchone() is not None

    def fatura_em_aberto(self, empresa_id: int) -> Linha | None:
        """A que vence primeiro entre as pendentes e as vencidas."""
        return self._linha("SELECT * FROM faturas WHERE empresa_id = ? AND status IN ('PENDING', 'OVERDUE') "
                           "ORDER BY vencimento LIMIT 1", empresa_id)

    def fatura_vencida(self, empresa_id: int) -> Linha | None:
        """A vencida mais antiga (o aviso no topo das páginas)."""
        return self._linha("SELECT * FROM faturas WHERE empresa_id = ? AND status = 'OVERDUE' ORDER BY vencimento LIMIT 1",
                           empresa_id)

    # -- a plataforma -------------------------------------------------------------------

    def empresas_com_uso(self) -> list[Linha]:
        """Todas as empresas, com quantas pessoas e propagandas têm e quanto ocupam."""
        return self._linhas(
            """
            SELECT e.*,
                   (SELECT COUNT(*) FROM usuarios u WHERE u.empresa_id = e.id) AS usuarios,
                   (SELECT COUNT(*) FROM propagandas p WHERE p.empresa_id = e.id) AS propagandas,
                   (SELECT COALESCE(SUM(tamanho), 0) FROM propagandas p WHERE p.empresa_id = e.id) AS bytes
            FROM empresas e ORDER BY e.id
            """)

    def contato_das_telas(self) -> list[Linha]:
        """De todas as telas: de que empresa são e quando falaram por último (para contar as online)."""
        return self._linhas("SELECT empresa_id, ultimo_contato, fechada_em FROM telas")

    def faturas_recentes(self, por_empresa: int) -> dict[int, list[Linha]]:
        """As últimas faturas de cada empresa."""
        resultado: dict[int, list[Linha]] = {}
        for fatura in self._linhas("SELECT * FROM faturas WHERE status != 'DELETED' ORDER BY vencimento DESC"):
            da_empresa = resultado.setdefault(fatura["empresa_id"], [])
            if len(da_empresa) < por_empresa:
                da_empresa.append(fatura)
        return resultado

    def receita_mensal(self) -> int:
        """Soma dos planos das assinaturas ativas (centavos por mês)."""
        linha = self._c.execute(
            "SELECT COALESCE(SUM(p.preco_centavos), 0) FROM empresas e JOIN planos p ON p.id = e.plano_id "
            "WHERE e.asaas_assinatura_id IS NOT NULL AND e.ativa = 1").fetchone()
        return int(linha[0])

    # -- exportação dos dados da loja (portabilidade, LGPD) -----------------------------

    def dados_para_exportar(self, empresa_id: int) -> dict[str, Any]:
        """Tudo da empresa, sem senhas nem segredos de 2FA."""
        empresa = self._linhas("SELECT id, nome, criado_em, alerta_emails, alerta_webhook FROM empresas WHERE id = ?",
                               empresa_id)
        return {
            "empresa": empresa[0] if empresa else {},
            "usuarios": self._linhas("SELECT usuario, papel, criado_em, totp_segredo IS NOT NULL AS dois_fatores "
                                     "FROM usuarios WHERE empresa_id = ?", empresa_id),
            "grupos": self._linhas("SELECT id, nome FROM grupos WHERE empresa_id = ?", empresa_id),
            "telas": self._linhas("SELECT id, nome, grupo_id, letreiro, ultimo_contato, criado_em FROM telas "
                                  "WHERE empresa_id = ?", empresa_id),
            "propagandas": self._linhas("SELECT id, nome, arquivo, tipo, tamanho, duracao, ativo, inicio, fim, dias_semana, "
                                        "hora_inicio, hora_fim, para_todas, posicao, criado_em FROM propagandas "
                                        "WHERE empresa_id = ? ORDER BY posicao", empresa_id),
            "destinos": self._linhas("SELECT d.* FROM propaganda_destinos d JOIN propagandas p ON p.id = d.propaganda_id "
                                     "WHERE p.empresa_id = ?", empresa_id),
            "configuracoes": self._linhas("SELECT chave, valor FROM configuracoes WHERE empresa_id = ?", empresa_id),
            "ponto": self._linhas("SELECT usuario_nome, entrada, saida, motivo_saida FROM ponto_registros "
                                  "WHERE empresa_id = ? ORDER BY entrada", empresa_id),
        }

    def exibicoes_para_exportar(self, empresa_id: int) -> Iterator[tuple[Any, ...]]:
        """Uma a uma (podem ser muitas): (exibido_em, tela_id, propaganda_id, propaganda, duração)."""
        for linha in self._c.execute("SELECT exibido_em, tela_id, propaganda_id, propaganda_nome, duracao FROM exibicoes "
                                     "WHERE empresa_id = ? ORDER BY exibido_em", (empresa_id,)):
            yield tuple(linha)
