"""A conta da loja na plataforma: página pública com os planos, cadastro, assinatura e início.

Fluxo do cliente:
1. Vê os planos em "/" (ou /planos) e cria a conta em /cadastro: a loja e o administrador dela.
2. No início da conta (/loja), vê os módulos: os assinados abrem; os outros mostram "Assinar".
3. Assina um plano: o Asaas cria a assinatura (PIX, boleto ou cartão). Os módulos liberam na hora;
   a primeira fatura vence depois do teste grátis. Sem pagamento, a suspensão automática que já
   existe (cobranca.avaliar_inadimplencia) bloqueia a loja depois da tolerância.
4. Pode trocar de plano ou cancelar a qualquer momento.
"""

import logging
import re

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from src.domain.cobranca import ErroDeCobranca, ErroNoGateway

from . import asaas, cobranca, db, modulos
from .auth import EMPRESA_PRINCIPAL, ErroUsuario, _buscar_usuario, _entrar, criar_usuario, existe_usuario, login_obrigatorio

bp = Blueprint("conta", __name__)
log = logging.getLogger("propagandas.conta")

SLUG_VALIDO = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
SLUGS_RESERVADOS = {"admin", "api", "plataforma", "login", "entrar", "cadastro", "conta", "planos", "static", "suporte"}


def planos_a_venda(conexao):
    linhas = conexao.execute(
        "SELECT * FROM planos WHERE ativo = 1 AND preco_centavos > 0 ORDER BY preco_centavos, nome"
    ).fetchall()
    return [dict(linha, lista_modulos=[m for m in modulos.MODULOS if m in modulos.ler(linha["modulos"])]) for linha in linhas]


def _empresa():
    return db.obter().execute("SELECT * FROM empresas WHERE id = ?", (g.empresa_id,)).fetchone()


@bp.before_app_request
def pagina_publica():
    """Com o cadastro aberto, quem chega em "/" sem login vê a apresentação e os planos."""
    if (request.path == "/" and getattr(g, "usuario", None) is None and current_app.config["CADASTRO_ABERTO"]
            and existe_usuario(db.obter())):
        return render_template("publico.html", planos=planos_a_venda(db.obter()))
    return None


@bp.route("/planos")
def planos():
    return render_template("publico.html", planos=planos_a_venda(db.obter()), so_planos=True)


# ---------------------------------------------------------------------------
# Cadastro da loja
# ---------------------------------------------------------------------------

@bp.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if not current_app.config["CADASTRO_ABERTO"]:
        abort(404)
    if g.usuario is not None:
        return redirect(url_for("conta.inicio"))
    conexao = db.obter()
    plano_id = request.values.get("plano", "")
    dados = {campo: request.form.get(campo, "").strip() for campo in ("loja", "codigo", "usuario", "email")}
    if request.method == "POST":
        erro = None
        nome = dados["loja"][:100]
        codigo = dados["codigo"].lower()
        if not nome:
            erro = "Informe o nome da loja."
        elif codigo and (not SLUG_VALIDO.match(codigo) or codigo in SLUGS_RESERVADOS):
            erro = "O código da loja usa só letras minúsculas, números e hífen (de 3 a 40), ex.: padeiro-lanches."
        elif codigo and conexao.execute("SELECT 1 FROM empresas WHERE slug = ?", (codigo,)).fetchone():
            erro = f"O código “{codigo}” já é de outra loja. Escolha outro."
        elif "@" not in dados["email"] or len(dados["email"]) > 200:
            erro = "Informe um e-mail válido (é para onde vão as faturas e avisos)."
        elif request.form.get("senha", "") != request.form.get("confirmacao", ""):
            erro = "As senhas não conferem."
        elif request.form.get("termos") != "on":
            erro = "Para criar a conta, aceite os Termos de Uso e a Política de Privacidade."
        if erro is None:
            with conexao:
                empresa_id = conexao.execute(
                    "INSERT INTO empresas (nome, slug, email_cobranca) VALUES (?, ?, ?)",
                    (nome, codigo or db.gerar_slug(conexao, nome), dados["email"]),
                ).lastrowid
            try:
                usuario_id = criar_usuario(conexao, empresa_id, dados["usuario"], request.form.get("senha", ""), "admin")
            except ErroUsuario as erro_usuario:
                with conexao:
                    conexao.execute("DELETE FROM empresas WHERE id = ?", (empresa_id,))
                erro = str(erro_usuario)
            else:
                _entrar(_buscar_usuario(conexao, usuario_id))
                log.info("Nova loja cadastrada: “%s” (id %s, IP %s)", nome, empresa_id, request.remote_addr)
                cobranca._avisar_plataforma(f"🆕 Nova loja cadastrada: “{nome}”.")
                flash("Conta criada! Agora escolha o plano da sua loja.", "ok")
                if plano_id.isdigit():
                    return redirect(url_for("conta.assinar", plano_id=int(plano_id)))
                return redirect(url_for("conta.inicio"))
        flash(erro, "erro")
    return render_template("cadastro.html", dados=dados, plano_id=plano_id), (400 if request.method == "POST" else 200)


# ---------------------------------------------------------------------------
# Início da conta
# ---------------------------------------------------------------------------

@bp.route("/loja")
@login_obrigatorio()
def inicio():
    conexao = db.obter()
    empresa = _empresa()
    plano = None
    if empresa["plano_id"]:
        plano = conexao.execute("SELECT * FROM planos WHERE id = ?", (empresa["plano_id"],)).fetchone()
    return render_template(
        "conta_inicio.html",
        empresa=empresa,
        plano=plano,
        planos=planos_a_venda(conexao),
        MODULOS=modulos.MODULOS,
        DESCRICOES=modulos.DESCRICOES,
        fatura_aberta=conexao.execute(
            "SELECT * FROM faturas WHERE empresa_id = ? AND status IN ('PENDING', 'OVERDUE') ORDER BY vencimento LIMIT 1",
            (g.empresa_id,),
        ).fetchone(),
        principal=g.empresa_id == EMPRESA_PRINCIPAL,
    )


# ---------------------------------------------------------------------------
# Assinar, trocar de plano e cancelar (só o administrador da loja)
# ---------------------------------------------------------------------------

def _plano(conexao, plano_id):
    plano = conexao.execute(
        "SELECT * FROM planos WHERE id = ? AND ativo = 1 AND preco_centavos > 0", (plano_id,)
    ).fetchone()
    if plano is None:
        abort(404)
    return plano


@bp.route("/loja/assinar/<int:plano_id>", methods=["GET", "POST"])
@login_obrigatorio("admin")
def assinar(plano_id):
    conexao = db.obter()
    plano = _plano(conexao, plano_id)
    empresa = _empresa()
    if g.empresa_id == EMPRESA_PRINCIPAL:
        flash("A empresa principal da plataforma usa todos os módulos e não assina planos.", "erro")
        return redirect(url_for("conta.inicio"))
    if empresa["plano_id"] == plano["id"] and empresa["asaas_assinatura_id"]:
        flash(f"Sua loja já está no plano {plano['nome']}.", "ok")
        return redirect(url_for("conta.inicio"))
    dias = current_app.config["TESTE_GRATIS_DIAS"]

    if request.method == "POST":
        try:
            plano_novo, acao = cobranca.servico().assinar(
                g.empresa_id, plano["id"], request.form.get("documento"), request.form.get("email"), dias,
                gateway_configurado=asaas.configurado(),
            )
        except ErroDeCobranca as erro:
            flash(str(erro), "erro")
            return redirect(url_for("conta.assinar", plano_id=plano_id))
        log.info("“%s” %s plano “%s” (empresa %s)", g.usuario["usuario"], acao, plano_novo.nome, g.empresa_id)
        flash(f"Plano {plano_novo.nome} ativo! Os módulos já estão liberados.", "ok")
        return redirect(url_for("conta.inicio"))

    return render_template("assinar.html", plano=plano, empresa=empresa, dias=dias,
                           lista_modulos=[m for m in modulos.MODULOS if m in modulos.ler(plano["modulos"])],
                           MODULOS=modulos.MODULOS, troca=bool(empresa["asaas_assinatura_id"]))


@bp.route("/loja/cancelar", methods=["POST"])
@login_obrigatorio("admin")
def cancelar():
    empresa = _empresa()
    if not empresa["plano_id"]:
        return redirect(url_for("conta.inicio"))
    try:
        cobranca.servico().desistir(g.empresa_id)
    except ErroNoGateway as erro:
        flash(f"Não foi possível cancelar agora ({erro}). Tente de novo ou fale com o suporte.", "erro")
        return redirect(url_for("conta.inicio"))
    log.info("“%s” cancelou a assinatura da empresa %s", g.usuario["usuario"], g.empresa_id)
    flash("Assinatura cancelada. Nenhuma nova fatura será gerada. Os dados da loja continuam guardados.", "ok")
    return redirect(url_for("conta.inicio"))
