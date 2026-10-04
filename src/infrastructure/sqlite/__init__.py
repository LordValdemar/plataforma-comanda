"""Repositórios no SQLite."""

from .cardapio import RepositorioDeCardapioSQLite
from .cobranca import RepositorioDeCobrancaSQLite
from .comandas import RepositorioDeComandasSQLite
from .contas import RepositorioDeContasSQLite
from .empresas import RepositorioDeEmpresasSQLite
from .exibicoes import RepositorioDeExibicoesSQLite
from .permissoes import RepositorioDePermissoesSQLite
from .ponto import RepositorioDePontoSQLite
from .propagandas import RepositorioDePropagandasSQLite
from .telas import RepositorioDeConexoesSQLite, RepositorioDeTelasSQLite
from .vendas import RepositorioDeVendasSQLite

__all__ = [
    "RepositorioDeExibicoesSQLite", "RepositorioDeVendasSQLite",
    "RepositorioDeCardapioSQLite",
    "RepositorioDeCobrancaSQLite", "RepositorioDeContasSQLite", "RepositorioDeEmpresasSQLite",
    "RepositorioDeComandasSQLite", "RepositorioDePermissoesSQLite", "RepositorioDePontoSQLite",
    "RepositorioDeConexoesSQLite", "RepositorioDePropagandasSQLite", "RepositorioDeTelasSQLite",
]
