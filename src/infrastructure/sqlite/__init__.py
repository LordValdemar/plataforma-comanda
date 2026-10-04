"""Repositórios no SQLite."""

from .cobranca import RepositorioDeCobrancaSQLite
from .comandas import RepositorioDeComandasSQLite
from .contas import RepositorioDeContasSQLite
from .permissoes import RepositorioDePermissoesSQLite
from .ponto import RepositorioDePontoSQLite
from .propagandas import RepositorioDePropagandasSQLite
from .telas import RepositorioDeConexoesSQLite, RepositorioDeTelasSQLite

__all__ = [
    "RepositorioDeCobrancaSQLite", "RepositorioDeContasSQLite",
    "RepositorioDeComandasSQLite", "RepositorioDePermissoesSQLite", "RepositorioDePontoSQLite",
    "RepositorioDeConexoesSQLite", "RepositorioDePropagandasSQLite", "RepositorioDeTelasSQLite",
]
