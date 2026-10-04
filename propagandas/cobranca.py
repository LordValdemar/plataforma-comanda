"""Cobrança na porta de entrada: webhook do Asaas, página de pagamento e telas da plataforma.

As regras (planos, assinatura, faturas, bloqueio por atraso, desconfiar do webhook) ficam em
src/domain/cobranca; o Asaas em src/infrastructure/asaas.py; aqui ficam as rotas.
"""

import hmac
import logging
from datetime import date

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from src.domain.cobranca import (
    EM_ABERTO,
    STATUS,
    DadosDoPlano,
    ErroDeCobranca,
    ErroNoGateway,
    ServicoDeCobranca,
    ler_preco,
    referencia,
)
from src.domain.dinheiro import reais
from src.domain.documentos import documento_valido, so_numeros
from src.domain.erros import NaoEncontrado
from src.infrastructure.sqlite import RepositorioDeCobrancaSQLite

from . import agenda, alertas, asaas, db, modulos
from .auth import EMPRESA_PRINCIPAL, csrf_isento, login_obrigatorio, plataforma_obrigatoria

__all__ = ["EM_ABERTO", "STATUS", "documento_valido", "ler_reais", "reais", "referencia", "so_numeros"]

bp = Blueprint("cobranca", __name__)
log = logging.getLogger("propagandas.cobranca")
ler_reais = ler_preco   # nome antigo: preço digitado → centavos (None se inválido)


def _avisar_plataforma(mensagem):
    principal = db.obter().execute("SELECT * FROM empresas WHERE id = ?", (EMPRESA_PRINCIPAL,)).fetchone()
    canais = alertas.canais_da_empresa(principal, current_app.config)
    if canais.nomes:
        alertas.enviar_alerta(mensagem, canais)


def servico():
    return ServicoDeCobranca(
        RepositorioDeCobrancaSQLite(db.obter()), asaas.cliente(), empresa_principal=EMPRESA_PRINCIPAL,
        tolerancia_dias=current_app.config["COBRANCA_TOLERANCIA_DIAS"], hoje=lambda: agenda.agora_local().date(),
        relogio=lambda: agenda.agora_utc(), avisar_plataforma=_avisar_plataforma,
    )


# Atalhos com os nomes de antes (tarefas, outras telas e testes).

def faturas_da_empresa(conexao, empresa_id, limite=12):
    return conexao.execute(
        "SELECT * FROM faturas WHERE empresa_id = ? AND status != 'DELETED' ORDER BY vencimento DESC LIMIT ?",
        (empresa_id, limite),
    ).fetchall()


def fatura_vencida(conexao, empresa_id):
    """A fatura vencida mais antiga, se houver (aviso no topo das páginas)."""
    return conexao.execute(
        "SELECT * FROM faturas WHERE empresa_id = ? AND status = 'OVERDUE' ORDER BY vencimento LIMIT 1", (empresa_id,)
    ).fetchone()


def gravar_fatura(_conexao, empresa_id, pagamento):
    servico().gravar_fatura(empresa_id, pagamento)


def avaliar_inadimplencia(_conexao, empresa_id, hoje=None):
    return servico().avaliar_inadimplencia(empresa_id, hoje)


def sincronizar(_conexao, empresa):
    cobranca = servico()
    return cobranca.sincronizar(cobranca.empresa(empresa["id"]))


def sincronizar_todas():
    servico().sincronizar_todas()


# ---------------------------------------------------------------------------
# Webhook do Asaas
# ---------------------------------------------------------------------------

@bp.route("/webhooks/asaas", methods=["POST"])
@csrf_isento
def webhook_asaas():
    esperado = current_app.config["ASAAS_WEBHOOK_TOKEN"]
    recebido = request.headers.get("asaas-access-token", "")
    if not esperado or not hmac.compare_digest(recebido, esperado):
        log.warning("Webhook do Asaas com token inválido (IP %s)", request.remote_addr)
        abort(401)
    evento = request.get_json(silent=True)
    if not isinstance(evento, dict):
        abort(400)
    # Sempre 200 para eventos válidos (mesmo os ignorados): erros repetidos fazem o Asaas pausar a fila.
    return servico().processar_aviso(evento)


# ---------------------------------------------------------------------------
# Cliente: página de pagamento (acessível mesmo com a empresa suspensa)
# ---------------------------------------------------------------------------

@bp.route("/pagamento")
@login_obrigatorio()
def pagamento():
    conexao = db.obter()
    empresa = conexao.execute("SELECT * FROM empresas WHERE id = ?", (g.empresa_id,)).fetchone()
    return render_template("pagamento.html", empresa=empresa, faturas=faturas_da_empresa(conexao, g.empresa_id),
                           STATUS=STATUS, EM_ABERTO=EM_ABERTO)


# ---------------------------------------------------------------------------
# Plataforma: planos e assinaturas
# ---------------------------------------------------------------------------

def _plano_do_formulario():
    limites = []
    for campo in ("limite_telas", "limite_mb"):
        valor = request.form.get(campo, "").strip()
        limites.append(int(valor) if valor.isdigit() else None)
    return DadosDoPlano(
        nome=request.form.get("nome", ""), preco_centavos=ler_preco(request.form.get("preco")),
        limite_telas=limites[0], limite_mb=limites[1], modulos=modulos.do_formulario(request.form),
        descricao=request.form.get("descricao", ""), ativo=request.form.get("ativo") == "on",
    )


def _na_plataforma(acao, sucesso=None):
    """Roda a ação; erro de regra vira aviso, empresa ou plano inexistente vira 404."""
    try:
        resultado = acao(servico())
    except NaoEncontrado:
        abort(404)
    except ErroDeCobranca as erro:
        flash(str(erro), "erro")
    else:
        if sucesso:
            flash(sucesso(resultado) if callable(sucesso) else sucesso, "ok")
    return redirect(url_for("plataforma.lista"))


@bp.route("/plataforma/planos/novo", methods=["POST"])
@plataforma_obrigatoria
def novo_plano():
    dados = _plano_do_formulario()

    def criar(cobranca):
        cobranca.criar_plano(dados)
        log.info("“%s” criou o plano “%s” (%s)", g.usuario["usuario"], dados.nome.strip(), reais(dados.preco_centavos))
        return dados.nome.strip()[:60]
    return _na_plataforma(criar, lambda nome: f"Plano “{nome}” criado.")


@bp.route("/plataforma/planos/<int:plano_id>/atualizar", methods=["POST"])
@plataforma_obrigatoria
def atualizar_plano(plano_id):
    return _na_plataforma(lambda cobranca: cobranca.atualizar_plano(plano_id, _plano_do_formulario()),
                          "Plano atualizado. Assinaturas já ativas mantêm o preço até você mudar o plano delas.")


@bp.route("/plataforma/empresas/<int:empresa_id>/cobranca", methods=["POST"])
@plataforma_obrigatoria
def salvar_cobranca(empresa_id):
    """Plano, CPF/CNPJ e e-mail de cobrança. Se a assinatura já existe, atualiza o valor no Asaas."""
    plano_id = request.form.get("plano_id", "")

    def salvar(cobranca):
        cobranca.salvar_dados(empresa_id, int(plano_id) if plano_id.isdigit() else None, request.form.get("documento"),
                              request.form.get("email_cobranca"), request.form.get("cobranca_automatica") == "on")
        log.info("“%s” alterou a cobrança da empresa %s", g.usuario["usuario"], empresa_id)
    return _na_plataforma(salvar, "Dados de cobrança salvos.")


@bp.route("/plataforma/empresas/<int:empresa_id>/cobranca/ativar", methods=["POST"])
@plataforma_obrigatoria
def ativar_cobranca(empresa_id):
    try:
        vencimento = date.fromisoformat(request.form.get("primeiro_vencimento", ""))
    except ValueError:
        vencimento = None

    def ativar(cobranca):
        plano = cobranca.ativar(empresa_id, vencimento)
        log.info("“%s” ativou a cobrança da empresa %s (%s/mês)", g.usuario["usuario"], empresa_id,
                 reais(plano.preco_centavos))
        return plano
    return _na_plataforma(ativar, lambda plano: f"Cobrança ativada: {reais(plano.preco_centavos)}/mês. "
                                                "O Asaas envia as faturas ao cliente.")


@bp.route("/plataforma/empresas/<int:empresa_id>/cobranca/cancelar", methods=["POST"])
@plataforma_obrigatoria
def cancelar_cobranca(empresa_id):
    def cancelar(cobranca):
        try:
            empresa = cobranca.cancelar(empresa_id)
        except ErroNoGateway as erro:
            raise ErroDeCobranca(f"O Asaas recusou o cancelamento: {erro}") from None
        log.info("“%s” cancelou a cobrança de “%s”", g.usuario["usuario"], empresa.nome)
    return _na_plataforma(cancelar, "Cobrança cancelada. Nenhuma nova fatura será gerada.")


@bp.route("/plataforma/empresas/<int:empresa_id>/cobranca/sincronizar", methods=["POST"])
@plataforma_obrigatoria
def sincronizar_empresa(empresa_id):
    def sincronizar_uma(cobranca):
        try:
            return cobranca.sincronizar(cobranca.empresa(empresa_id))
        except ErroNoGateway as erro:
            raise ErroDeCobranca(f"Falha ao consultar o Asaas: {erro}") from None
    return _na_plataforma(sincronizar_uma, lambda total: f"{total} fatura(s) atualizada(s).")
