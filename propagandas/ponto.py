"""Controle de ponto (porta de entrada web): a equipe só usa o sistema com o ponto aberto e no horário.

- O administrador da loja liga o controle (fica desligado até alguém ligar).
- Com ele ligado, quem não é administrador precisa "registrar a entrada" para usar o sistema.
- Cada pessoa pode ter um horário (dias e faixa de horas). Fora dele não dá para registrar a
  entrada, e o ponto aberto fecha sozinho quando o horário acaba.
- O administrador vê quem está trabalhando, desconecta qualquer pessoa (todos os aparelhos)
  e tira o relatório de horas.
- QR code (ligado por padrão): um aparelho fixo na loja mostra um QR que muda a cada 2 minutos
  e é de uso único. Só depois de ler o QR com o celular a pessoa registra a entrada; assim
  ninguém bate ponto de casa. A saída também pede o QR; sem ele, fica anotada no relatório.

As regras ficam em src/domain/ponto; aqui ficam as rotas, a sessão (a presença confirmada
pelo QR) e o before_request que segura quem está sem ponto aberto.
"""

import csv
import io
import logging
import time
from datetime import timedelta

import segno
from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from src.domain.erros import NaoEncontrado, SemPermissao
from src.domain.horario import Horario, HorarioInvalido, ler_dias, ler_hora
from src.domain.periodo import Periodo
from src.domain.ponto import (
    MAX_CODIGOS_ERRADOS,
    QR_TROCA_SEGUNDOS,
    ErroDePonto,
    Funcionario,
    ServicoDePonto,
)
from src.domain.tentativas import LimiteDeTentativas
from src.infrastructure.sqlite import ConsultasDoPonto, RepositorioDePontoSQLite

from . import agenda, db, modulos
from .auth import login_obrigatorio
from .comanda.formatos import hoje_local, intervalo_utc

__all__ = ["MAX_CODIGOS_ERRADOS", "QR_TROCA_SEGUNDOS", "bp", "exigir", "fechar_fora_do_horario"]

bp = Blueprint("ponto", __name__)
log = logging.getLogger("propagandas.ponto")

MAX_DIAS_RELATORIO = 92
PRESENCA_SEGUNDOS = 300     # depois de ler o QR, 5 minutos para tocar em "Registrar"
_codigos_errados = LimiteDeTentativas(MAX_CODIGOS_ERRADOS, 600)  # por pessoa, neste processo

# O que quem está sem ponto aberto ainda pode abrir.
LIBERADAS_SEM_PONTO = {
    "ponto.meu", "ponto.entrada", "ponto.saida", "ponto.ler_qr", "ponto.digitar_codigo", "ponto.quiosque",
    "ponto.quiosque_api", "auth.sair", "auth.login", "auth.minha_conta", "auth.minha_senha", "auth.ativar_2fa",
    "auth.desativar_2fa_proprio", "static", "legal.privacidade", "legal.termos", "legal.security_txt",
}


def servico(empresa_id=None):
    repositorio = RepositorioDePontoSQLite(db.obter(), g.empresa_id if empresa_id is None else empresa_id)
    return ServicoDePonto(repositorio, agenda.fuso(), relogio=agenda.agora_utc, tentativas=_codigos_errados)


def funcionario(usuario):
    """Uma linha de usuarios → Funcionario (para as regras do ponto)."""
    return Funcionario(
        id=usuario["id"], nome=usuario["usuario"], papel=usuario["papel"], plataforma=bool(usuario["plataforma"]),
        exige_ponto=bool(usuario["exige_ponto"]),
        horario=Horario(usuario["horario_dias"] or "", usuario["horario_inicio"], usuario["horario_fim"]),
    )


# Atalhos usados pelos testes e pela tela da equipe.

def no_horario(usuario, agora=None):
    horario = Horario(usuario["horario_dias"] or "", usuario["horario_inicio"], usuario["horario_fim"])
    return horario.vale(agora or agenda.agora_local())


def resumo_horario(usuario):
    return Horario(usuario["horario_dias"] or "", usuario["horario_inicio"], usuario["horario_fim"]).resumo


def token_qr(empresa_id, agora=None):
    return servico(empresa_id).token_atual(agora)


def token_valido(empresa_id, token, agora=None):
    return servico(empresa_id).situacao(token, agora) == "valido"


# ---------------------------------------------------------------------------
# Antes de cada pedido e a tarefa de fundo
# ---------------------------------------------------------------------------

def presenca_confirmada():
    """A pessoa leu o QR da própria loja há pouco (guardado na sessão dela)?"""
    presenca = session.get("ponto_presenca") or {}
    return presenca.get("empresa") == g.empresa_id and presenca.get("ate", 0) > time.time()


def _sair_desta_sessao():
    """Sai só deste aparelho; o aviso ("Saída registrada às...") continua para a tela de login."""
    guardar = {chave: session[chave] for chave in ("csrf", "_flashes") if chave in session}
    session.clear()
    session.update(guardar)


def exigir():
    """before_request: sem ponto aberto (ou fora do horário), a pessoa só vê a página do ponto."""
    usuario = getattr(g, "usuario", None)
    g.ponto_aberto = None
    g.ponto_exige = False
    if usuario is None:
        return None
    ponto, pessoa = servico(), funcionario(usuario)
    g.ponto_exige = ponto.bate_ponto(pessoa)
    if not g.ponto_exige:
        return None
    registro, fechou = ponto.conferir_expediente(pessoa)
    if fechou:
        log.info("Ponto de “%s” fechado: fim do horário", pessoa.nome)
        flash("Seu horário de trabalho terminou e o ponto foi encerrado.", "erro")
    g.ponto_aberto = registro
    if registro or request.endpoint in LIBERADAS_SEM_PONTO:
        return None
    if "/api/" in request.path:
        return {"erro": "registre a entrada no ponto"}, 401  # a tela da cozinha recarrega e cai no ponto
    return redirect(url_for("ponto.meu"))


def fechar_fora_do_horario():
    """Tarefa de fundo (a cada minuto): fecha os pontos de quem passou do horário, loja por loja."""
    for loja in ConsultasDoPonto(db.obter()).lojas_com_ponto_aberto():
        for nome in servico(loja).fechar_fora_do_horario():
            log.info("Ponto de “%s” fechado automaticamente: fim do horário", nome)


# ---------------------------------------------------------------------------
# Funcionário: registrar entrada e saída
# ---------------------------------------------------------------------------

def _periodo_utc(inicio, fim):
    de, ate = intervalo_utc(inicio, fim)
    return agenda.de_texto_utc(de), agenda.de_texto_utc(ate)


@bp.route("/ponto")
@login_obrigatorio()
def meu():
    if g.usuario["papel"] == "admin":
        return redirect(url_for("ponto.equipe"))
    ponto, pessoa = servico(), funcionario(g.usuario)
    hoje = hoje_local()
    return render_template(
        "ponto.html",
        exige=ponto.bate_ponto(pessoa),
        registro=ponto.aberto(pessoa.id),
        no_horario=ponto.no_horario(pessoa),
        exige_qr=ponto.exige_qr,
        presenca=presenca_confirmada(),
        horario=pessoa.horario.resumo,
        registros=ponto.historico(*_periodo_utc(hoje - timedelta(days=6), hoje), pessoa.id),
        inicio=modulos.pagina_inicial(),
    )


@bp.route("/ponto/entrada", methods=["POST"])
@login_obrigatorio()
def entrada():
    ponto, pessoa = servico(), funcionario(g.usuario)
    if not ponto.bate_ponto(pessoa):
        return redirect(modulos.pagina_inicial())
    try:
        abriu = ponto.registrar_entrada(pessoa, presenca_confirmada(), request.remote_addr or "")
    except ErroDePonto as erro:
        flash(str(erro), "erro")
        return redirect(url_for("ponto.meu"))
    session.pop("ponto_presenca", None)  # o QR lido vale para um registro só
    if abriu:
        log.info("“%s” registrou a entrada", pessoa.nome)
        flash(f"Entrada registrada às {ponto.agora_local():%H:%M}. Bom trabalho!", "ok")
    return redirect(modulos.pagina_inicial())


@bp.route("/ponto/saida", methods=["POST"])
@login_obrigatorio()
def saida():
    ponto, pessoa = servico(), funcionario(g.usuario)
    fechou = ponto.registrar_saida(pessoa, presenca_confirmada())
    session.pop("ponto_presenca", None)
    if fechou:
        log.info("“%s” registrou a saída", pessoa.nome)
        flash(f"Saída registrada às {ponto.agora_local():%H:%M}. Até a próxima!", "ok")
    _sair_desta_sessao()
    return redirect(url_for("auth.login"))


@bp.route("/ponto/codigo", methods=["POST"])
@login_obrigatorio()
def digitar_codigo():
    """Para quando a câmera não abre: a pessoa digita o código curto que aparece embaixo do QR."""
    return _confirmar_presenca(lambda ponto: ponto.usar_codigo(request.form.get("codigo"), g.usuario["id"]))


@bp.route("/ponto/qr/<slug>/<token>")
@login_obrigatorio()
def ler_qr(slug, token):
    """Endereço do QR code da loja: o celular abre, e a presença fica confirmada por alguns minutos."""
    if slug != g.usuario["empresa_slug"]:
        flash("Este QR code é de outra loja.", "erro")
        return redirect(url_for("ponto.meu"))
    return _confirmar_presenca(lambda ponto: ponto.usar_token(token))


def _confirmar_presenca(usar):
    try:
        usar(servico())
    except ErroDePonto as erro:
        flash(str(erro), "erro")
    else:
        session["ponto_presenca"] = {"empresa": g.empresa_id, "ate": time.time() + PRESENCA_SEGUNDOS}
        flash("QR code lido. Confirme abaixo.", "ok")
    return redirect(url_for("ponto.meu"))


def _empresa_do_quiosque(codigo):
    empresa_id = ConsultasDoPonto(db.obter()).loja_do_quiosque(codigo)
    if empresa_id is None:
        abort(404)
    return empresa_id


@bp.route("/ponto/quiosque/<codigo>")
def quiosque(codigo):
    """Tela fixa da loja (TV, tablet ou computador do caixa) que mostra o QR do ponto. Não precisa de login."""
    empresa_id = _empresa_do_quiosque(codigo)
    return render_template("ponto_quiosque.html", codigo=codigo, nome=ConsultasDoPonto(db.obter()).nome_da_loja(empresa_id))


@bp.route("/api/ponto/quiosque/<codigo>")
def quiosque_api(codigo):
    empresa_id = _empresa_do_quiosque(codigo)
    ponto = servico(empresa_id)
    if not ponto.ativo:
        resposta = {"ativo": False}
    else:
        # A tela pergunta a cada 2 s; o QR só vai na resposta quando mudou (outra versão).
        token = ponto.token_atual()
        versao = token.rsplit("-", 1)[0]
        resposta = {"ativo": True, "versao": versao,
                    "troca_em": QR_TROCA_SEGUNDOS - int(time.time()) % QR_TROCA_SEGUNDOS}
        if request.args.get("versao") != versao:
            resposta["codigo"] = ponto.codigo_digitavel(token)
            slug = ConsultasDoPonto(db.obter()).codigo_da_loja(empresa_id)
            endereco = url_for("ponto.ler_qr", slug=slug, token=token, _external=True)
            resposta["qr"] = segno.make(endereco, error="m").svg_data_uri(scale=10, border=2)
    resposta = jsonify(resposta)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


# ---------------------------------------------------------------------------
# Administrador: equipe, horários, desconectar e relatório
# ---------------------------------------------------------------------------

def _ler_periodo():
    hoje = hoje_local()
    periodo = Periodo.ler(request.args.get("de"), request.args.get("ate"), hoje - timedelta(days=6), hoje,
                          MAX_DIAS_RELATORIO)
    return periodo.inicio, periodo.fim


@bp.route("/ponto/equipe")
@login_obrigatorio("admin")
def equipe():
    ponto = servico()
    inicio, fim = _ler_periodo()
    pessoa_id = request.args.get("pessoa", type=int)
    registros = ponto.historico(*_periodo_utc(inicio, fim), pessoa_id)
    pessoas = ConsultasDoPonto(db.obter()).equipe(g.empresa_id)
    return render_template(
        "ponto_equipe.html",
        ativo=ponto.ativo,
        exige_qr=ponto.exige_qr,
        endereco_quiosque=url_for("ponto.quiosque", codigo=ponto.codigo_quiosque(), _external=True),
        pessoas=pessoas,
        no_horario={p["id"]: ponto.no_horario(funcionario(p)) for p in pessoas},
        registros=registros,
        totais=ponto.totais(registros),
        inicio=inicio, fim=fim, pessoa_id=pessoa_id,
        dias=agenda.DIAS,
        resumo_horario=resumo_horario,
    )


@bp.route("/ponto/qr-ajustes", methods=["POST"])
@login_obrigatorio("admin")
def qr_ajustes():
    acao = request.form.get("acao")
    ponto = servico()
    if acao == "novo_endereco":
        ponto.codigo_quiosque(novo=True)
        flash("Novo endereço da tela do QR code criado. Abra o endereço novo no aparelho da loja.", "ok")
    elif acao in ("exigir", "dispensar"):
        ponto.exigir_qr(acao == "exigir")
        flash("QR code exigido para registrar o ponto." if acao == "exigir"
              else "QR code dispensado: o ponto passa a ser registrado por um botão, de qualquer lugar.", "ok")
    else:
        abort(400)
    log.info("“%s” alterou o QR code do ponto: %s", g.usuario["usuario"], acao)
    return redirect(url_for("ponto.equipe"))


@bp.route("/ponto/ajustes", methods=["POST"])
@login_obrigatorio("admin")
def ajustes():
    ligar = request.form.get("ativo") == "1"
    servico().ligar(ligar)
    log.info("“%s” %s o controle de ponto", g.usuario["usuario"], "ligou" if ligar else "desligou")
    if ligar:
        flash("Controle de ponto ligado. A equipe precisa registrar a entrada para usar o sistema.", "ok")
    else:
        flash("Controle de ponto desligado.", "ok")
    return redirect(url_for("ponto.equipe"))


@bp.route("/ponto/pessoa/<int:usuario_id>", methods=["POST"])
@login_obrigatorio("admin")
def salvar_pessoa(usuario_id):
    try:
        pessoa = servico().salvar_horario(
            usuario_id, ler_dias(request.form.getlist("dias")), ler_hora(request.form.get("inicio")),
            ler_hora(request.form.get("fim")), isento=bool(request.form.get("isento")),
        )
    except NaoEncontrado:
        abort(404)
    except HorarioInvalido as erro:
        flash(str(erro), "erro")
    else:
        log.info("“%s” alterou o horário de “%s”", g.usuario["usuario"], pessoa.nome)
        flash(f"Horário de “{pessoa.nome}” salvo.", "ok")
    return redirect(url_for("ponto.equipe"))


@bp.route("/ponto/pessoa/<int:usuario_id>/desconectar", methods=["POST"])
@login_obrigatorio("admin")
def desconectar_pessoa(usuario_id):
    try:
        pessoa = servico().desconectar(usuario_id, funcionario(g.usuario))
    except NaoEncontrado:
        abort(404)
    except SemPermissao:
        abort(403)
    except ErroDePonto as erro:
        flash(str(erro), "erro")
    else:
        log.info("“%s” desconectou “%s”", g.usuario["usuario"], pessoa.nome)
        flash(f"“{pessoa.nome}” foi desconectado de todos os aparelhos.", "ok")
    return redirect(url_for("auth.usuarios" if request.form.get("voltar") == "usuarios" else "ponto.equipe"))


@bp.route("/ponto/relatorio.csv")
@login_obrigatorio("admin")
def relatorio_csv():
    inicio, fim = _ler_periodo()
    registros = servico().historico(*_periodo_utc(inicio, fim), request.args.get("pessoa", type=int))
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(["Pessoa", "Entrada", "Saída", "Horas trabalhadas", "Como saiu"])
    for r in reversed(registros):
        escritor.writerow([
            r.usuario_nome, agenda.local_formatado(agenda.para_texto_utc(r.entrada)),
            agenda.local_formatado(agenda.para_texto_utc(r.saida)) if r.saida else "(trabalhando)",
            f"{r.segundos / 3600:.2f}".replace(".", ","), r.motivo_saida or "",
        ])
    nome = f"ponto-{inicio.isoformat()}-a-{fim.isoformat()}.csv"
    return Response("﻿" + saida.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={nome}"})
