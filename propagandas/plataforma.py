"""Administração da plataforma: empresas clientes, planos e suspensão (só para o dono do sistema).

As regras ficam em src/domain/empresas/plataforma.py; aqui ficam a lista e as rotas.
"""

import logging
import os

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from src.domain.cobranca import ErroNoGateway
from src.domain.empresas import CadastroInvalido, DadosDaEmpresa, Limites
from src.domain.erros import NaoEncontrado

from . import agenda, alertas, asaas, cobranca, db, modulos, planos
from .auth import EMPRESA_PRINCIPAL, ErroUsuario, criar_usuario, plataforma_obrigatoria
from .planos import MB

bp = Blueprint("plataforma", __name__, url_prefix="/plataforma")
log = logging.getLogger("propagandas.plataforma")


def _ler_limite(nome):
    """Campo vazio = sem limite."""
    valor = request.form.get(nome, "").strip()
    return int(valor) if valor.isdigit() else None


def _dados_do_formulario(modulos_padrao="painel"):
    return DadosDaEmpresa(
        nome=request.form.get("nome", ""), limites=Limites(_ler_limite("limite_telas"), _ler_limite("limite_mb")),
        modulos_liberados=modulos.do_formulario(request.form, modulos_padrao), cadastro=request.form,
    )


@bp.route("/")
@plataforma_obrigatoria
def lista():
    conexao = db.obter()
    leitura = planos.consultas(conexao)
    empresas = leitura.empresas_com_uso()
    resumo_telas = {}
    for tela in leitura.contato_das_telas():
        contagem = resumo_telas.setdefault(tela["empresa_id"], {"total": 0, "online": 0})
        contagem["total"] += 1
        contagem["online"] += alertas.esta_online(tela)
    return render_template(
        "plataforma.html",
        MODULOS=modulos.MODULOS,
        modulos_de={e["id"]: modulos.da_empresa(conexao, e["id"]) for e in empresas},
        ler_modulos=modulos.ler,
        empresas=empresas,
        telas=resumo_telas,
        MB=MB,
        principal=EMPRESA_PRINCIPAL,
        planos=leitura.planos(),
        faturas=leitura.faturas_recentes(por_empresa=6),
        STATUS=cobranca.STATUS,
        asaas_configurado=asaas.configurado(),
        ambiente=current_app.config["ASAAS_AMBIENTE"],
        hoje=agenda.agora_local().date(),
        receita=leitura.receita_mensal(),
    )


@bp.route("/empresas/nova", methods=["POST"])
@plataforma_obrigatoria
def nova():
    conexao = db.obter()
    try:
        empresa_id, _ = planos.servico_da_plataforma(conexao).criar(
            _dados_do_formulario(),
            lambda empresa_id: criar_usuario(conexao, empresa_id, request.form.get("usuario", ""),
                                             request.form.get("senha", ""), "admin"))
    except CadastroInvalido as erro:
        flash(str(erro), "erro")
    except ErroUsuario as erro:
        flash(f"Empresa não criada: {erro}", "erro")
    else:
        nome = request.form.get("nome", "").strip()[:100]
        log.info("“%s” criou a empresa “%s” (id %s)", g.usuario["usuario"], nome, empresa_id)
        flash(f"Empresa “{nome}” criada. Envie o usuário e a senha para o cliente acessar.", "ok")
    return redirect(url_for("plataforma.lista"))


@bp.route("/empresas/<int:empresa_id>/atualizar", methods=["POST"])
@plataforma_obrigatoria
def atualizar(empresa_id):
    plataforma = planos.servico_da_plataforma()
    try:
        empresa = plataforma.cliente(empresa_id)
        _, mudou = plataforma.atualizar(empresa_id, _dados_do_formulario(empresa.modulos_liberados),
                                        ativa=request.form.get("ativa") == "on")
    except NaoEncontrado:
        abort(404)
    if mudou:
        log.warning("“%s” %s a empresa “%s”", g.usuario["usuario"], "SUSPENDEU" if empresa.ativa else "reativou", empresa.nome)
    flash("Empresa atualizada.", "ok")
    return redirect(url_for("plataforma.lista"))


def _apagar_midia(arquivo):
    caminho = os.path.join(current_app.config["PASTA_MIDIA"], arquivo)
    if os.path.exists(caminho):
        os.remove(caminho)


@bp.route("/empresas/<int:empresa_id>/excluir", methods=["POST"])
@plataforma_obrigatoria
def excluir(empresa_id):
    try:
        empresa = planos.servico_da_plataforma().excluir(
            empresa_id, request.form.get("confirmacao", ""), cobranca.servico().cancelar_no_gateway, _apagar_midia)
    except NaoEncontrado:
        abort(404)
    except ErroNoGateway as erro:
        flash(f"Não foi possível cancelar a assinatura no Asaas ({erro}). A empresa não foi excluída.", "erro")
    except CadastroInvalido as erro:
        flash(str(erro), "erro")
    else:
        log.warning("“%s” EXCLUIU a empresa “%s” (id %s) e todos os seus dados", g.usuario["usuario"], empresa.nome,
                    empresa_id)
        flash(f"Empresa “{empresa.nome}” e todos os seus dados foram excluídos.", "ok")
    return redirect(url_for("plataforma.lista"))
