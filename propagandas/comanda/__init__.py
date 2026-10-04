"""Comanda: módulo de pedidos da plataforma (garçons, cozinha, caixa e relatórios de vendas).

Cada loja usa o módulo se ele estiver no plano dela (veja propagandas/modulos.py). Todas as
tabelas (cmd_*) têm a empresa dona, e toda consulta filtra pela empresa do usuário logado.
"""

from . import ajustes, cardapio, comandas, cozinha, formatos, relatorios
from .base import pode, pode_fechar_conta


def registrar(app):
    for modulo in (cardapio, comandas, cozinha, relatorios, ajustes):
        app.register_blueprint(modulo.bp)
    app.jinja_env.globals["pode"] = pode
    app.jinja_env.globals["pode_fechar_conta"] = pode_fechar_conta
    app.jinja_env.filters["data_hora"] = formatos.data_hora
    app.jinja_env.filters["hora"] = formatos.hora
    app.jinja_env.filters["data_extenso"] = formatos.data_extenso
    app.jinja_env.filters["minutos"] = formatos.minutos_desde
