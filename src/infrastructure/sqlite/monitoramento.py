"""Telas e empresas para o monitoramento de telas offline, no SQLite (todas as empresas: roda em segundo plano)."""

import sqlite3

from src.domain.alertas import EmpresaAvisada, TelaMonitorada

from .datas import de_texto


class RepositorioDeMonitoramentoSQLite:
    def __init__(self, conexao: sqlite3.Connection) -> None:
        self._c = conexao

    def empresas_ativas(self) -> list[EmpresaAvisada]:
        return [EmpresaAvisada(e["id"], e["nome"], e["alerta_emails"], e["alerta_webhook"])
                for e in self._c.execute("SELECT id, nome, alerta_emails, alerta_webhook FROM empresas WHERE ativa = 1")]

    def telas_com_contato(self) -> list[TelaMonitorada]:
        telas = []
        for t in self._c.execute("SELECT id, nome, empresa_id, ultimo_contato, alerta_offline FROM telas "
                                 "WHERE ultimo_contato IS NOT NULL ORDER BY id"):
            contato = de_texto(t["ultimo_contato"])
            if contato is not None:
                telas.append(TelaMonitorada(t["id"], t["nome"], t["empresa_id"], contato, bool(t["alerta_offline"])))
        return telas

    def marcar_aviso(self, tela_id: int, avisado: bool) -> None:
        with self._c:
            self._c.execute("UPDATE telas SET alerta_offline = ? WHERE id = ?", (1 if avisado else 0, tela_id))
