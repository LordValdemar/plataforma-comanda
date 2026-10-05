"""Repositórios no SQLite."""

from .cardapio import RepositorioDeCardapioSQLite
from .cobranca import RepositorioDeCobrancaSQLite
from .comandas import RepositorioDeComandasSQLite
from .consultas_comanda import ConsultasDaComanda
from .consultas_painel import ConsultasDoPainel
from .consultas_ponto import ConsultasDoPonto
from .contas import RepositorioDeContasSQLite
from .empresas import RepositorioDeEmpresasSQLite
from .exibicoes import RepositorioDeExibicoesSQLite, apagar_exibicoes_anteriores
from .monitoramento import RepositorioDeMonitoramentoSQLite
from .permissoes import RepositorioDePermissoesSQLite
from .ponto import RepositorioDePontoSQLite
from .propagandas import RepositorioDePropagandasSQLite
from .telas import RepositorioDeConexoesSQLite, RepositorioDeTelasSQLite
from .vendas import RepositorioDeVendasSQLite

__all__ = [
    "ConsultasDaComanda", "ConsultasDoPainel", "ConsultasDoPonto",
    "RepositorioDeCardapioSQLite", "RepositorioDeCobrancaSQLite", "RepositorioDeComandasSQLite",
    "RepositorioDeConexoesSQLite", "RepositorioDeContasSQLite", "RepositorioDeEmpresasSQLite",
    "RepositorioDeExibicoesSQLite", "RepositorioDeMonitoramentoSQLite", "RepositorioDePermissoesSQLite",
    "RepositorioDePontoSQLite", "RepositorioDePropagandasSQLite", "RepositorioDeTelasSQLite", "RepositorioDeVendasSQLite",
    "apagar_exibicoes_anteriores",
]
