"""Repositórios no SQLite."""

from .cobranca import RepositorioDeCobrancaSQLite
from .comandas import RepositorioDeComandasSQLite
from .permissoes import RepositorioDePermissoesSQLite
from .ponto import RepositorioDePontoSQLite
from .propagandas import RepositorioDePropagandasSQLite
from .telas import RepositorioDeConexoesSQLite, RepositorioDeTelasSQLite

__all__ = [
    "RepositorioDeCobrancaSQLite",
    "RepositorioDeComandasSQLite", "RepositorioDePermissoesSQLite", "RepositorioDePontoSQLite",
    "RepositorioDeConexoesSQLite", "RepositorioDePropagandasSQLite", "RepositorioDeTelasSQLite",
]
