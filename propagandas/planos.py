"""Empresas na porta de entrada: o serviço (src/domain/empresas) e os limites do plano de cada uma."""

from src.domain.empresas import MB, ServicoDaPlataforma, ServicoDeEmpresas
from src.infrastructure.alertas import motivo_para_recusar
from src.infrastructure.sqlite import ConsultasDeEmpresas, RepositorioDeEmpresasSQLite

from . import db

__all__ = ["MB", "cabe_no_armazenamento", "consultas", "empresa", "pode_cadastrar_tela", "servico", "servico_da_plataforma", "uso"]


def servico(conexao=None):
    from .auth import EMPRESA_PRINCIPAL  # evita importação circular

    return ServicoDeEmpresas(RepositorioDeEmpresasSQLite(conexao or db.obter()), EMPRESA_PRINCIPAL,
                             conferir_webhook=motivo_para_recusar)


def consultas(conexao=None):
    """O que as telas leem das empresas, planos e faturas."""
    return ConsultasDeEmpresas(conexao or db.obter())


def empresa(conexao, empresa_id):
    """A ficha completa (para as telas)."""
    return consultas(conexao).ficha(empresa_id)


def uso(conexao, empresa_id):
    return servico(conexao).uso(empresa_id)


def pode_cadastrar_tela(conexao, empresa_id):
    return servico(conexao).cabe_mais_uma_tela(empresa_id)


def cabe_no_armazenamento(conexao, empresa_id, bytes_novos):
    return servico(conexao).cabe_no_armazenamento(empresa_id, bytes_novos)


def servico_da_plataforma(conexao=None):
    from .auth import EMPRESA_PRINCIPAL  # evita importação circular

    conexao = conexao or db.obter()
    return ServicoDaPlataforma(RepositorioDeEmpresasSQLite(conexao), EMPRESA_PRINCIPAL,
                               codigo_livre=servico(conexao).codigo_livre)
