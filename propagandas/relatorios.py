"""Relatório de exibições (proof of play), com exportação CSV. As regras ficam em src/domain/relatorios."""

from datetime import timedelta

from flask import Blueprint, g, render_template, request

from src.domain.periodo import Periodo
from src.domain.relatorios import RelatorioDeExibicoes
from src.domain.relatorios.exibicoes import DIAS_PADRAO
from src.infrastructure.sqlite import ConsultasDoPainel, RepositorioDeExibicoesSQLite

from . import agenda, db, modulos, permissoes
from .comanda.relatorios import csv_para_baixar

bp = Blueprint("relatorios", __name__)
bp.before_request(modulos.exigir("painel"))  # só para lojas com o Painel no plano


def _ler_filtros():
    hoje = agenda.agora_local().date()
    periodo = Periodo.ler(request.args.get("de"), request.args.get("ate"), hoje - timedelta(days=DIAS_PADRAO - 1), hoje)
    tela = request.args.get("tela", "")
    return periodo.inicio, periodo.fim, int(tela) if tela.isdigit() else None


def _intervalo_utc(de, ate):
    return agenda.inicio_do_dia_utc(de), agenda.inicio_do_dia_utc(ate + timedelta(days=1))


def relatorio():
    return RelatorioDeExibicoes(RepositorioDeExibicoesSQLite(db.obter(), g.empresa_id))


@bp.route("/relatorios")
@permissoes.exigir("relatorios")
def resumo():
    de, ate, tela_id = _ler_filtros()
    dados = relatorio().resumo(*_intervalo_utc(de, ate), tela_id)
    telas = ConsultasDoPainel(db.obter(), g.empresa_id).telas_para_escolher()
    return render_template("relatorios.html", de=de, ate=ate, tela_id=tela_id, telas=telas,
                           por_propaganda=dados.por_propaganda, por_tela=dados.por_tela,
                           total_exibicoes=dados.total_exibicoes, total_tempo=dados.total_tempo)


@bp.route("/relatorios.csv")
@permissoes.exigir("relatorios")
def exportar():
    """Uma linha por dia, tela e propaganda. O dia é o local: usa o fuso do primeiro dia do período,
    o que é exato em regiões sem horário de verão (como o Brasil hoje)."""
    de, ate, tela_id = _ler_filtros()
    linhas = relatorio().planilha(*_intervalo_utc(de, ate), tela_id, agenda.offset_minutos(de))
    return csv_para_baixar(linhas, f"exibicoes_{de:%Y%m%d}_{ate:%Y%m%d}.csv")
