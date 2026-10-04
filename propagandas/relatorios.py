"""Relatório de exibições (proof of play), com exportação CSV."""

import csv
import io
from datetime import date, timedelta

from flask import Blueprint, Response, g, render_template, request

from . import agenda, db, modulos, permissoes

bp = Blueprint("relatorios", __name__)
bp.before_request(modulos.exigir("painel"))  # só para lojas com o Painel no plano


def _ler_filtros():
    hoje = agenda.agora_local().date()

    def data(nome, padrao):
        try:
            return date.fromisoformat(request.args.get(nome, ""))
        except ValueError:
            return padrao

    de = data("de", hoje - timedelta(days=6))
    ate = data("ate", hoje)
    if ate < de:
        de, ate = ate, de
    tela = request.args.get("tela", "")
    tela_id = int(tela) if tela.isdigit() else None
    return de, ate, tela_id


def _where(de, ate, tela_id):
    condicoes = ["e.empresa_id = ?", "e.exibido_em >= ?", "e.exibido_em < ?"]
    parametros = [g.empresa_id, agenda.inicio_do_dia_utc(de), agenda.inicio_do_dia_utc(ate + timedelta(days=1))]
    if tela_id is not None:
        condicoes.append("e.tela_id = ?")
        parametros.append(tela_id)
    return " AND ".join(condicoes), parametros


@bp.route("/relatorios")
@permissoes.exigir("relatorios")
def resumo():
    de, ate, tela_id = _ler_filtros()
    where, parametros = _where(de, ate, tela_id)
    conexao = db.obter()

    por_propaganda = conexao.execute(
        f"""
        SELECT e.propaganda_id,
               COALESCE(p.nome, MAX(e.propaganda_nome)) AS nome,
               p.id IS NULL AS excluida,
               COUNT(*) AS exibicoes,
               SUM(e.duracao) AS tempo,
               COUNT(DISTINCT e.tela_id) AS telas
        FROM exibicoes e LEFT JOIN propagandas p ON p.id = e.propaganda_id
        WHERE {where}
        GROUP BY e.propaganda_id
        ORDER BY exibicoes DESC
        """,
        parametros,
    ).fetchall()

    por_tela = conexao.execute(
        f"""
        SELECT COALESCE(t.nome, '(tela excluída)') AS nome, COUNT(*) AS exibicoes, SUM(e.duracao) AS tempo
        FROM exibicoes e LEFT JOIN telas t ON t.id = e.tela_id
        WHERE {where}
        GROUP BY e.tela_id
        ORDER BY exibicoes DESC
        """,
        parametros,
    ).fetchall()

    return render_template(
        "relatorios.html",
        de=de,
        ate=ate,
        tela_id=tela_id,
        telas=conexao.execute("SELECT id, nome FROM telas WHERE empresa_id = ? ORDER BY nome", (g.empresa_id,)).fetchall(),
        por_propaganda=por_propaganda,
        por_tela=por_tela,
        total_exibicoes=sum(linha["exibicoes"] for linha in por_propaganda),
        total_tempo=sum(linha["tempo"] for linha in por_propaganda),
    )


@bp.route("/relatorios.csv")
@permissoes.exigir("relatorios")
def exportar():
    """Uma linha por dia, tela e propaganda. Abre direto no Excel (separador ;)."""
    de, ate, tela_id = _ler_filtros()
    where, parametros = _where(de, ate, tela_id)
    # Converte UTC para o dia local. Usa o fuso do primeiro dia do período,
    # o que é exato em regiões sem horário de verão (como o Brasil hoje).
    ajuste = f"{agenda.offset_minutos(de):+d} minutes"
    linhas = db.obter().execute(
        f"""
        SELECT date(e.exibido_em, ?) AS dia,
               COALESCE(t.nome, '(tela excluída)') AS tela,
               COALESCE(p.nome, MAX(e.propaganda_nome)) AS propaganda,
               COUNT(*) AS exibicoes,
               SUM(e.duracao) AS tempo
        FROM exibicoes e
        LEFT JOIN telas t ON t.id = e.tela_id
        LEFT JOIN propagandas p ON p.id = e.propaganda_id
        WHERE {where}
        GROUP BY dia, e.tela_id, e.propaganda_id
        ORDER BY dia, tela, propaganda
        """,
        [ajuste, *parametros],
    )

    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(["Data", "Tela", "Propaganda", "Exibições", "Tempo total (segundos)"])
    for linha in linhas:
        dia = date.fromisoformat(linha["dia"]).strftime("%d/%m/%Y")
        escritor.writerow([dia, linha["tela"], linha["propaganda"], linha["exibicoes"], round(linha["tempo"])])

    nome = f"exibicoes_{de:%Y%m%d}_{ate:%Y%m%d}.csv"
    return Response(
        "﻿" + saida.getvalue(),  # BOM: faz o Excel reconhecer os acentos
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )
