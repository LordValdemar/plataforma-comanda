"""Controle de ponto: a equipe só usa o sistema com o ponto aberto e dentro do horário de trabalho.

- O administrador da loja liga o controle (fica desligado até alguém ligar).
- Com ele ligado, quem não é administrador precisa "registrar a entrada" para usar o sistema.
- Cada pessoa pode ter um horário (dias e faixa de horas). Fora dele não dá para registrar a
  entrada, e o ponto aberto fecha sozinho quando o horário acaba.
- O administrador vê quem está trabalhando, desconecta qualquer pessoa (todos os aparelhos)
  e tira o relatório de horas.
"""

import csv
import io
import logging
import secrets
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, session, url_for

from . import agenda, db, modulos
from .auth import login_obrigatorio
from .comanda.formatos import hoje_local, intervalo_utc

bp = Blueprint("ponto", __name__)
log = logging.getLogger("propagandas.ponto")

MAX_DIAS_RELATORIO = 92

# O que quem está sem ponto aberto ainda pode abrir.
LIBERADAS_SEM_PONTO = {
    "ponto.meu", "ponto.entrada", "ponto.saida", "auth.sair", "auth.login", "auth.minha_conta", "auth.minha_senha",
    "auth.ativar_2fa", "auth.desativar_2fa_proprio", "static", "legal.privacidade", "legal.termos", "legal.security_txt",
}


# ---------------------------------------------------------------------------
# Regras
# ---------------------------------------------------------------------------

def ativo(empresa_id):
    return db.ler_config(empresa_id, "ponto_ativo") == "1"


def exige_ponto(usuario):
    """Administradores nunca batem ponto; os outros, se a loja ligou o controle e a pessoa não é isenta."""
    if usuario["papel"] == "admin" or usuario["plataforma"] or not usuario["exige_ponto"]:
        return False
    return ativo(usuario["empresa_id"])


def no_horario(usuario, agora=None):
    """A pessoa está dentro do horário de trabalho? Sem faixa de horas, vale o dia inteiro.

    Um turno que vira a noite (ex.: 18:00 às 02:00) pertence ao dia em que começa: o
    sábado às 01:00 conta como o turno de sexta.
    """
    agora = agora or agenda.agora_local()
    dias = usuario["horario_dias"] or agenda.TODOS_OS_DIAS
    inicio, fim = usuario["horario_inicio"], usuario["horario_fim"]
    dia = str(agora.weekday())
    if not inicio or not fim:
        return dia in dias
    hora = agora.strftime("%H:%M")
    if inicio < fim:
        return dia in dias and inicio <= hora < fim
    if hora >= inicio:
        return dia in dias
    return hora < fim and str((agora.weekday() - 1) % 7) in dias


def resumo_horario(usuario):
    dias = agenda.resumo_dias(usuario["horario_dias"])
    return f"{dias}, {agenda.resumo_horario(usuario['horario_inicio'], usuario['horario_fim'])}"


def aberto(conexao, usuario_id):
    return conexao.execute(
        "SELECT * FROM ponto_registros WHERE usuario_id = ? AND saida IS NULL", (usuario_id,)
    ).fetchone()


def fechar(conexao, usuario_id, motivo, encerrado_por=None):
    """Fecha o ponto aberto da pessoa (se houver). Chame fora de transação."""
    with conexao:
        return conexao.execute(
            "UPDATE ponto_registros SET saida = ?, motivo_saida = ?, encerrado_por = ? "
            "WHERE usuario_id = ? AND saida IS NULL",
            (agenda.para_texto_utc(agenda.agora_utc()), motivo, encerrado_por, usuario_id),
        ).rowcount


def desconectar(conexao, usuario, motivo, por):
    """Encerra as sessões da pessoa em todos os aparelhos e fecha o ponto dela."""
    with conexao:
        conexao.execute("UPDATE usuarios SET token_sessao = ? WHERE id = ?", (secrets.token_hex(16), usuario["id"]))
    fechar(conexao, usuario["id"], motivo, por)


def _sair_desta_sessao():
    csrf = session.get("csrf")
    session.clear()
    if csrf:
        session["csrf"] = csrf


def exigir():
    """before_request: sem ponto aberto (ou fora do horário), a pessoa só vê a página do ponto."""
    usuario = getattr(g, "usuario", None)
    g.ponto_exige = usuario is not None and exige_ponto(usuario)
    g.ponto_aberto = None
    if not g.ponto_exige:
        return None
    conexao = db.obter()
    registro = aberto(conexao, usuario["id"])
    if registro and not no_horario(usuario):
        fechar(conexao, usuario["id"], "fim do horário")
        log.info("Ponto de “%s” fechado: fim do horário", usuario["usuario"])
        flash("Seu horário de trabalho terminou e o ponto foi encerrado.", "erro")
        registro = None
    g.ponto_aberto = registro
    if registro or request.endpoint in LIBERADAS_SEM_PONTO:
        return None
    if "/api/" in request.path:
        return {"erro": "registre a entrada no ponto"}, 401  # a tela da cozinha recarrega e cai no ponto
    return redirect(url_for("ponto.meu"))


def fechar_fora_do_horario():
    """Tarefa de fundo (a cada minuto): fecha os pontos de quem passou do horário sem registrar a saída."""
    conexao = db.obter()
    linhas = conexao.execute(
        "SELECT u.* FROM ponto_registros p JOIN usuarios u ON u.id = p.usuario_id WHERE p.saida IS NULL"
    ).fetchall()
    for usuario in linhas:
        if exige_ponto(usuario) and not no_horario(usuario):
            fechar(conexao, usuario["id"], "fim do horário")
            log.info("Ponto de “%s” fechado automaticamente: fim do horário", usuario["usuario"])


# ---------------------------------------------------------------------------
# Funcionário: registrar entrada e saída
# ---------------------------------------------------------------------------

def _historico(conexao, empresa_id, de, ate, usuario_id=None):
    filtro, parametros = "", [empresa_id, de, ate]
    if usuario_id:
        filtro, parametros = " AND usuario_id = ?", parametros + [usuario_id]
    linhas = conexao.execute(
        "SELECT p.*, u.usuario AS encerrado_por_nome FROM ponto_registros p "
        "LEFT JOIN usuarios u ON u.id = p.encerrado_por "
        f"WHERE p.empresa_id = ? AND p.entrada >= ? AND p.entrada < ?{filtro} ORDER BY p.entrada DESC",
        parametros,
    ).fetchall()
    agora = agenda.agora_utc()
    registros = []
    for linha in linhas:
        fim = agenda.de_texto_utc(linha["saida"]) if linha["saida"] else agora
        registros.append({**dict(linha), "segundos": int((fim - agenda.de_texto_utc(linha["entrada"])).total_seconds())})
    return registros


@bp.route("/ponto")
@login_obrigatorio()
def meu():
    if g.usuario["papel"] == "admin":
        return redirect(url_for("ponto.equipe"))
    conexao = db.obter()
    hoje = hoje_local()
    de, ate = intervalo_utc(hoje - timedelta(days=6), hoje)
    return render_template(
        "ponto.html",
        exige=exige_ponto(g.usuario),
        registro=aberto(conexao, g.usuario["id"]),
        no_horario=no_horario(g.usuario),
        horario=resumo_horario(g.usuario),
        registros=_historico(conexao, g.empresa_id, de, ate, g.usuario["id"]),
        inicio=modulos.pagina_inicial(),
    )


@bp.route("/ponto/entrada", methods=["POST"])
@login_obrigatorio()
def entrada():
    conexao = db.obter()
    if not exige_ponto(g.usuario):
        return redirect(modulos.pagina_inicial())
    if not no_horario(g.usuario):
        flash(f"Fora do seu horário de trabalho ({resumo_horario(g.usuario)}). Fale com o administrador.", "erro")
        return redirect(url_for("ponto.meu"))
    if aberto(conexao, g.usuario["id"]) is None:
        with conexao:
            conexao.execute(
                "INSERT OR IGNORE INTO ponto_registros (empresa_id, usuario_id, usuario_nome, entrada, ip) "
                "VALUES (?, ?, ?, ?, ?)",
                (g.empresa_id, g.usuario["id"], g.usuario["usuario"], agenda.para_texto_utc(agenda.agora_utc()),
                 (request.remote_addr or "")[:45]),
            )
        log.info("“%s” registrou a entrada", g.usuario["usuario"])
        flash(f"Entrada registrada às {agenda.agora_local():%H:%M}. Bom trabalho!", "ok")
    return redirect(modulos.pagina_inicial())


@bp.route("/ponto/saida", methods=["POST"])
@login_obrigatorio()
def saida():
    if fechar(db.obter(), g.usuario["id"], "saída"):
        log.info("“%s” registrou a saída", g.usuario["usuario"])
        flash(f"Saída registrada às {agenda.agora_local():%H:%M}. Até a próxima!", "ok")
    _sair_desta_sessao()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Administrador: equipe, horários, desconectar e relatório
# ---------------------------------------------------------------------------

def _ler_periodo():
    hoje = hoje_local()
    def ler(nome, padrao):
        try:
            return date.fromisoformat(request.args.get(nome, ""))
        except ValueError:
            return padrao
    inicio, fim = ler("de", hoje - timedelta(days=6)), ler("ate", hoje)
    if fim < inicio:
        inicio, fim = fim, inicio
    if (fim - inicio).days >= MAX_DIAS_RELATORIO:
        inicio = fim - timedelta(days=MAX_DIAS_RELATORIO - 1)
    return inicio, fim


def _pessoa(conexao, usuario_id):
    pessoa = conexao.execute(
        "SELECT * FROM usuarios WHERE id = ? AND empresa_id = ?", (usuario_id, g.empresa_id)
    ).fetchone()
    if pessoa is None:
        abort(404)
    return pessoa


@bp.route("/ponto/equipe")
@login_obrigatorio("admin")
def equipe():
    conexao = db.obter()
    inicio, fim = _ler_periodo()
    de, ate = intervalo_utc(inicio, fim)
    pessoa_id = request.args.get("pessoa", type=int)
    registros = _historico(conexao, g.empresa_id, de, ate, pessoa_id)
    totais = {}
    for r in registros:
        totais[r["usuario_nome"]] = totais.get(r["usuario_nome"], 0) + r["segundos"]
    pessoas = conexao.execute(
        "SELECT u.*, p.entrada AS trabalhando_desde FROM usuarios u "
        "LEFT JOIN ponto_registros p ON p.usuario_id = u.id AND p.saida IS NULL "
        "WHERE u.empresa_id = ? ORDER BY u.papel = 'admin', u.usuario",
        (g.empresa_id,),
    ).fetchall()
    return render_template(
        "ponto_equipe.html",
        ativo=ativo(g.empresa_id),
        pessoas=pessoas,
        no_horario={p["id"]: no_horario(p) for p in pessoas},
        registros=registros,
        totais=sorted(totais.items()),
        inicio=inicio, fim=fim, pessoa_id=pessoa_id,
        dias=agenda.DIAS,
        resumo_horario=resumo_horario,
    )


@bp.route("/ponto/ajustes", methods=["POST"])
@login_obrigatorio("admin")
def ajustes():
    ligar = request.form.get("ativo") == "1"
    db.gravar_config(g.empresa_id, "ponto_ativo", "1" if ligar else "")
    log.info("“%s” %s o controle de ponto", g.usuario["usuario"], "ligou" if ligar else "desligou")
    if ligar:
        flash("Controle de ponto ligado. A equipe precisa registrar a entrada para usar o sistema.", "ok")
    else:
        flash("Controle de ponto desligado.", "ok")
    return redirect(url_for("ponto.equipe"))


@bp.route("/ponto/pessoa/<int:usuario_id>", methods=["POST"])
@login_obrigatorio("admin")
def salvar_pessoa(usuario_id):
    conexao = db.obter()
    pessoa = _pessoa(conexao, usuario_id)
    dias = "".join(d for d in agenda.TODOS_OS_DIAS if d in request.form.getlist("dias"))
    inicio = _ler_hora(request.form.get("inicio"))
    fim = _ler_hora(request.form.get("fim"))
    if not dias:
        flash(f"“{pessoa['usuario']}”: escolha pelo menos um dia de trabalho.", "erro")
    elif bool(inicio) != bool(fim) or (inicio and inicio == fim):
        flash(f"“{pessoa['usuario']}”: informe o horário de início e de fim (diferentes), ou deixe os dois em branco.", "erro")
    else:
        with conexao:
            conexao.execute(
                "UPDATE usuarios SET horario_dias = ?, horario_inicio = ?, horario_fim = ?, exige_ponto = ? WHERE id = ?",
                (dias, inicio, fim, 0 if request.form.get("isento") else 1, usuario_id),
            )
        log.info("“%s” alterou o horário de “%s”", g.usuario["usuario"], pessoa["usuario"])
        flash(f"Horário de “{pessoa['usuario']}” salvo.", "ok")
    return redirect(url_for("ponto.equipe"))


def _ler_hora(valor):
    valor = (valor or "").strip()
    try:
        return datetime.strptime(valor, "%H:%M").strftime("%H:%M") if valor else None
    except ValueError:
        return None


@bp.route("/ponto/pessoa/<int:usuario_id>/desconectar", methods=["POST"])
@login_obrigatorio("admin")
def desconectar_pessoa(usuario_id):
    conexao = db.obter()
    pessoa = _pessoa(conexao, usuario_id)
    if pessoa["id"] == g.usuario["id"]:
        flash("Para sair, use o botão Sair.", "erro")
    elif pessoa["plataforma"]:
        abort(403)
    else:
        desconectar(conexao, pessoa, "desconectado pelo administrador", g.usuario["id"])
        log.info("“%s” desconectou “%s”", g.usuario["usuario"], pessoa["usuario"])
        flash(f"“{pessoa['usuario']}” foi desconectado de todos os aparelhos.", "ok")
    return redirect(url_for("auth.usuarios" if request.form.get("voltar") == "usuarios" else "ponto.equipe"))


@bp.route("/ponto/relatorio.csv")
@login_obrigatorio("admin")
def relatorio_csv():
    inicio, fim = _ler_periodo()
    de, ate = intervalo_utc(inicio, fim)
    registros = _historico(db.obter(), g.empresa_id, de, ate, request.args.get("pessoa", type=int))
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(["Pessoa", "Entrada", "Saída", "Horas trabalhadas", "Como saiu"])
    for r in reversed(registros):
        escritor.writerow([
            r["usuario_nome"], agenda.local_formatado(r["entrada"]),
            agenda.local_formatado(r["saida"]) if r["saida"] else "(trabalhando)",
            f"{r['segundos'] / 3600:.2f}".replace(".", ","), r["motivo_saida"] or "",
        ])
    nome = f"ponto-{inicio.isoformat()}-a-{fim.isoformat()}.csv"
    return Response("﻿" + saida.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={nome}"})
