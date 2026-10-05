"""Login (com 2FA opcional), usuários, permissões, empresas e proteção CSRF."""

import hmac
import logging
import secrets
import time
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from src.domain.contas import (
    PAPEIS,
    SENHA_MINIMA,
    Bloqueado,
    CodigoIncorreto,
    EmpresaSuspensa,
    ErroUsuario,
    LojaAmbigua,
    PapelInvalido,
    ServicoDeContas,
    acesso,
)
from src.domain.contas.entidades import JANELA_BLOQUEIO, MAX_TENTATIVAS
from src.domain.empresas import ServicoDeEmpresas
from src.domain.erros import NaoEncontrado, SemPermissao
from src.domain.tentativas import LimiteDeTentativas
from src.infrastructure.senhas import SenhasWerkzeug
from src.infrastructure.sqlite import ConsultasDeEmpresas, RepositorioDeContasSQLite, RepositorioDeEmpresasSQLite

from . import db, modulos, totp

__all__ = ["PAPEIS", "SENHA_MINIMA", "ErroUsuario", "criar_usuario", "desativar_2fa", "trocar_senha"]

bp = Blueprint("auth", __name__)
log = logging.getLogger("propagandas.auth")

EMPRESA_PRINCIPAL = 1  # a empresa de quem instalou o sistema
VALIDADE_ETAPA_2FA = 5 * 60
_tentativas = LimiteDeTentativas(MAX_TENTATIVAS, JANELA_BLOQUEIO)   # senha e código 2FA, por IP (neste processo)


def servico(conexao=None):
    """As regras das contas (src/domain/contas), com o banco e o guardador de senhas desta instalação."""
    return ServicoDeContas(RepositorioDeContasSQLite(conexao or db.obter()), SenhasWerkzeug(), _tentativas,
                           relogio=lambda: totp.agora())  # lido a cada uso: os testes trocam o relógio do 2FA


# Atalhos com os nomes de antes (painel, plataforma, cadastro e gerenciar.py).

def criar_usuario(conexao, empresa_id, usuario, senha, papel="editor", plataforma=False):
    return servico(conexao).criar(empresa_id, usuario, senha, papel, plataforma)


def trocar_senha(conexao, usuario_id, senha_nova):
    servico(conexao).trocar_senha(usuario_id, senha_nova)


def desativar_2fa(conexao, usuario_id):
    servico(conexao).desativar_2fa(usuario_id)


def existe_usuario(conexao):
    return servico(conexao).existe_alguem()


# ---------------------------------------------------------------------------
# Sessão, CSRF e decoradores
# ---------------------------------------------------------------------------

def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_isento(funcao):
    """Para rotas chamadas pelas TVs, que se identificam pelo código da tela."""
    funcao.csrf_isento = True
    return funcao


def _verificar_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    rota = current_app.view_functions.get(request.endpoint)
    if getattr(rota, "csrf_isento", False):
        return
    # Formulários mandam o campo csrf_token; a tela da cozinha (fetch) manda o cabeçalho.
    enviado = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    esperado = session.get("csrf", "")
    if not esperado or not hmac.compare_digest(enviado, esperado):
        log.warning("CSRF inválido em %s vindo de %s", request.path, request.remote_addr)
        abort(400)


def _buscar_usuario(conexao, usuario_id):
    """A pessoa com o nome, o código e a situação da loja dela."""
    return ConsultasDeEmpresas(conexao).usuario_com_loja(usuario_id)


def suspensa_por_pagamento(linha):
    """Empresa suspensa por falta de pagamento: entra, mas só vê a página de pagamento."""
    return acesso(bool(linha["empresa_ativa"]), linha["motivo_suspensao"], bool(linha["plataforma"])) == "so_pagamento"


def _acesso_suspenso(linha):
    return acesso(bool(linha["empresa_ativa"]), linha["motivo_suspensao"], bool(linha["plataforma"])) == "suspenso"


# Páginas liberadas para empresas suspensas por falta de pagamento.
LIBERADAS_SEM_PAGAMENTO = {
    "cobranca.pagamento", "auth.sair", "auth.minha_conta", "auth.minha_senha", "auth.ativar_2fa",
    "auth.desativar_2fa_proprio", "legal.privacidade", "legal.termos", "static", "conta.inicio",
}


def _carregar_usuario():
    g.usuario = None
    g.empresa_id = None
    g.bloqueio_pagamento = False
    usuario_id = session.get("usuario_id")
    if usuario_id is None:
        return
    linha = _buscar_usuario(db.obter(), usuario_id)
    valido = linha and hmac.compare_digest(linha["token_sessao"], session.get("token", ""))
    if valido and _acesso_suspenso(linha):
        flash("O acesso desta empresa está suspenso. Fale com o suporte.", "erro")
        valido = False
    if valido:
        g.usuario = linha
        g.empresa_id = linha["empresa_id"]
        g.bloqueio_pagamento = suspensa_por_pagamento(linha)
    else:
        csrf = session.get("csrf")
        session.clear()
        if csrf:
            session["csrf"] = csrf


def _restringir_se_bloqueado():
    if g.bloqueio_pagamento and request.endpoint not in LIBERADAS_SEM_PAGAMENTO:
        return redirect(url_for("cobranca.pagamento"))
    return None


def login_obrigatorio(papel=None):
    def decorador(funcao):
        @wraps(funcao)
        def verificar(*args, **kwargs):
            if g.usuario is None:
                if not existe_usuario(db.obter()):
                    return redirect(url_for("auth.configurar"))
                return redirect(url_for("auth.login", proximo=request.full_path.rstrip("?")))
            if papel and g.usuario["papel"] != papel:
                abort(403)
            return funcao(*args, **kwargs)

        return verificar

    return decorador


def plataforma_obrigatoria(funcao):
    """Só para quem administra a plataforma (todas as empresas)."""
    @wraps(funcao)
    @login_obrigatorio()
    def verificar(*args, **kwargs):
        if not g.usuario["plataforma"]:
            abort(403)
        return funcao(*args, **kwargs)

    return verificar


def _destino_seguro(proximo, usuario):
    # Evita redirecionar para outro site (open redirect).
    if proximo and proximo.startswith("/") and not proximo.startswith("//") and "\\" not in proximo:
        return proximo
    return modulos.pagina_inicial(usuario, modulos.do_usuario(usuario, modulos.da_empresa(db.obter(), usuario["empresa_id"])))


def _entrar(usuario):
    session.clear()
    session.permanent = True
    session["usuario_id"] = usuario["id"]
    session["token"] = usuario["token_sessao"]


def registrar(app):
    app.register_blueprint(bp)
    app.before_request(_verificar_csrf)
    app.before_request(_carregar_usuario)
    app.before_request(_restringir_se_bloqueado)
    app.before_request(modulos.carregar)
    app.jinja_env.globals["csrf_token"] = token_csrf
    app.jinja_env.globals["PAPEIS"] = PAPEIS

    @app.after_request
    def nao_guardar_paginas_restritas(resposta):
        if getattr(g, "usuario", None) is not None and resposta.mimetype == "text/html":
            resposta.headers["Cache-Control"] = "no-store"
        return resposta


# ---------------------------------------------------------------------------
# Primeiro acesso, login e 2FA
# ---------------------------------------------------------------------------

@bp.route("/configurar", methods=["GET", "POST"])
def configurar():
    """Primeiro acesso: cria o administrador. Some depois que existe um usuário."""
    conexao = db.obter()
    if existe_usuario(conexao):
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        usuario = request.form.get("usuario", "")
        senha = request.form.get("senha", "")
        empresa = request.form.get("empresa", "").strip()[:100]
        if senha != request.form.get("confirmacao", ""):
            flash("As senhas não conferem.", "erro")
        else:
            try:
                novo_id = criar_usuario(conexao, EMPRESA_PRINCIPAL, usuario, senha, "admin", plataforma=True)
            except ErroUsuario as erro:
                flash(str(erro), "erro")
            else:
                ServicoDeEmpresas(RepositorioDeEmpresasSQLite(conexao), EMPRESA_PRINCIPAL).renomear(
                    EMPRESA_PRINCIPAL, empresa)
                _entrar(_buscar_usuario(conexao, novo_id))
                log.info("Administrador inicial “%s” criado (IP %s)", usuario.strip(), request.remote_addr)
                flash("Tudo pronto! Agora cadastre suas telas e envie as propagandas.", "ok")
                return redirect(url_for("painel.lista"))
    return render_template("configurar.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    conexao = db.obter()
    if not existe_usuario(conexao):
        return redirect(url_for("auth.configurar"))
    loja = (request.form.get("loja") or request.args.get("loja") or "").strip().lower()
    if g.usuario is not None:
        return redirect(modulos.pagina_inicial())

    if request.method == "POST":
        ip = request.remote_addr or "?"
        usuario = request.form.get("usuario", "").strip()
        try:
            conta = servico(conexao).entrar(usuario, request.form.get("senha", ""), loja, ip)
        except Bloqueado as erro:
            log.warning("Login bloqueado por excesso de tentativas (IP %s)", ip)
            flash(str(erro), "erro")
            return render_template("login.html", usuario=usuario, loja=loja), 429
        except LojaAmbigua as erro:
            flash(str(erro), "erro")
            return render_template("login.html", usuario=usuario, loja=loja, pedir_loja=True), 401
        except EmpresaSuspensa as erro:
            log.warning("Login recusado: empresa suspensa (“%s”, IP %s)", usuario, ip)
            flash(str(erro), "erro")
            return render_template("login.html", usuario=usuario, loja=loja), 403
        except ErroUsuario as erro:
            log.warning("Senha errada para “%s” (IP %s)", usuario, ip)
            flash(str(erro), "erro")
            return render_template("login.html", usuario=usuario, loja=loja, pedir_loja=bool(loja)), 401
        proximo = request.args.get("proximo")
        if conta.tem_2fa:
            # Senha certa, mas ainda falta o código do aplicativo.
            session.clear()
            session["2fa_usuario"] = conta.id
            session["2fa_desde"] = time.time()
            session["2fa_proximo"] = proximo or ""
            return redirect(url_for("auth.login_codigo"))
        linha = _buscar_usuario(conexao, conta.id)
        _entrar(linha)
        log.info("Login de “%s” (IP %s)", conta.usuario, ip)
        return redirect(_destino_seguro(proximo, linha))

    return render_template("login.html", usuario="", loja=loja, pedir_loja=bool(loja))


@bp.route("/entrar/<slug>")
def entrar_na_loja(slug):
    """Endereço de login de cada loja (o código já vem preenchido): bom para o ícone no celular da equipe."""
    if not ConsultasDeEmpresas(db.obter()).loja_existe(slug):
        abort(404)
    return redirect(url_for("auth.login", loja=slug.lower()))


@bp.route("/login/codigo", methods=["GET", "POST"])
def login_codigo():
    usuario_id = session.get("2fa_usuario")
    if not usuario_id or time.time() - session.get("2fa_desde", 0) > VALIDADE_ETAPA_2FA:
        session.pop("2fa_usuario", None)
        flash("Entre de novo com usuário e senha.", "erro")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        ip = request.remote_addr or "?"
        conexao = db.obter()
        try:
            conta = servico(conexao).confirmar_codigo(usuario_id, request.form.get("codigo", ""), ip)
        except Bloqueado as erro:
            flash(str(erro), "erro")
            return render_template("login_codigo.html"), 429
        except CodigoIncorreto as erro:
            log.warning("Código 2FA errado (usuário id %s, IP %s)", usuario_id, ip)
            flash(str(erro), "erro")
            return render_template("login_codigo.html"), 401
        linha = _buscar_usuario(conexao, conta.id)
        proximo = session.get("2fa_proximo")
        _entrar(linha)
        log.info("Login de “%s” com 2FA (IP %s)", conta.usuario, ip)
        return redirect(_destino_seguro(proximo, linha))

    return render_template("login_codigo.html")


@bp.route("/sair", methods=["POST"])
def sair():
    # Com o ponto aberto, sair é registrar a saída (na página do ponto, com ou sem o QR code).
    # Com a loja bloqueada por falta de pagamento a página do ponto não abre: aí sai direto,
    # e o ponto é fechado sozinho no fim do horário.
    if g.get("ponto_aberto") and not g.get("bloqueio_pagamento"):
        flash("Você está com o ponto aberto. Para sair, registre a saída.", "erro")
        return redirect(url_for("ponto.meu"))
    session.clear()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Minha conta: senha e 2FA
# ---------------------------------------------------------------------------

def _conta_logada():
    return servico().conta(g.usuario["id"])


def _continuar_neste_aparelho():
    """O token mudou (senha nova, 2FA): este aparelho continua; os outros saem."""
    _entrar(_buscar_usuario(db.obter(), g.usuario["id"]))


@bp.route("/conta")
@login_obrigatorio()
def minha_conta():
    segredo = qr = None
    conta = _conta_logada()
    if not conta.tem_2fa:
        segredo = servico().segredo_para_ativar(conta)
        qr = totp.qr_code(segredo, conta.usuario, current_app.config["NOME_PLATAFORMA"])
    return render_template("conta.html", segredo=segredo, qr=qr)


@bp.route("/conta/senha", methods=["POST"])
@login_obrigatorio()
def minha_senha():
    try:
        servico().mudar_minha_senha(_conta_logada(), request.form.get("atual", ""), request.form.get("nova", ""),
                                    request.form.get("confirmacao", ""))
    except ErroUsuario as erro:
        flash(str(erro), "erro")
    else:
        _continuar_neste_aparelho()
        log.info("“%s” trocou a própria senha", g.usuario["usuario"])
        flash("Senha alterada. Os outros aparelhos foram desconectados.", "ok")
    return redirect(url_for("auth.minha_conta"))


@bp.route("/conta/2fa/ativar", methods=["POST"])
@login_obrigatorio()
def ativar_2fa():
    try:
        servico().ativar_2fa(_conta_logada(), request.form.get("codigo", ""))
    except ErroUsuario as erro:
        flash(str(erro), "erro")
        return redirect(url_for("auth.minha_conta"))
    _continuar_neste_aparelho()
    log.info("“%s” ativou a verificação em duas etapas", g.usuario["usuario"])
    flash("Verificação em duas etapas ativada. Os outros aparelhos foram desconectados.", "ok")
    return redirect(url_for("auth.minha_conta"))


@bp.route("/conta/2fa/desativar", methods=["POST"])
@login_obrigatorio()
def desativar_2fa_proprio():
    try:
        servico().desativar_2fa_propria(_conta_logada(), request.form.get("senha", ""), request.form.get("codigo", ""))
    except ErroUsuario as erro:
        flash(str(erro), "erro")
    else:
        _continuar_neste_aparelho()
        log.info("“%s” desativou a verificação em duas etapas", g.usuario["usuario"])
        flash("Verificação em duas etapas desativada.", "ok")
    return redirect(url_for("auth.minha_conta"))


# ---------------------------------------------------------------------------
# Usuários da empresa (administradores)
# ---------------------------------------------------------------------------

def _na_equipe(acao):
    """Roda a ação na equipe da loja: alguém de outra loja dá 404; o que não pode, 403."""
    try:
        return acao(servico())
    except NaoEncontrado:
        abort(404)
    except SemPermissao:
        abort(403)
    except PapelInvalido:
        abort(400)


@bp.route("/usuarios")
@login_obrigatorio("admin")
def usuarios():
    return render_template("usuarios.html", usuarios=ConsultasDeEmpresas(db.obter()).usuarios_da_loja(g.empresa_id))


@bp.route("/usuarios/novo", methods=["POST"])
@login_obrigatorio("admin")
def novo_usuario():
    try:
        servico().criar_na_loja(g.empresa_id, g.modulos_empresa, request.form.get("usuario", ""),
                                request.form.get("senha", ""), request.form.get("papel", "editor"))
    except ErroUsuario as erro:
        flash(str(erro), "erro")
    else:
        log.info("“%s” criou o usuário “%s”", g.usuario["usuario"], request.form.get("usuario", "").strip())
        flash("Usuário criado.", "ok")
    return redirect(url_for("auth.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/editar", methods=["POST"])
@login_obrigatorio("admin")
def editar_usuario(usuario_id):
    """Troca o papel de alguém da equipe e, se preenchida, define uma senha nova (para quem esqueceu)."""
    def editar(contas):
        try:
            return contas.editar(g.empresa_id, g.usuario["id"], usuario_id, request.form.get("papel"),
                                 request.form.get("senha", ""), g.modulos_empresa)
        except ErroUsuario as erro:   # papel ou senha inválidos (não pode / não existe seguem para o _na_equipe)
            flash(str(erro), "erro")
            return None
    resultado = _na_equipe(editar)
    if resultado is not None:
        alvo, mudancas = resultado
        log.info("“%s” editou “%s”: %s", g.usuario["usuario"], alvo.usuario, "; ".join(mudancas) or "nada")
        flash(f"“{alvo.usuario}” {' e '.join(mudancas)}." if mudancas else "Nada mudou.", "ok")
    return redirect(url_for("auth.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/excluir", methods=["POST"])
@login_obrigatorio("admin")
def excluir_usuario(usuario_id):
    def excluir(contas):
        try:
            alvo = contas.excluir(g.empresa_id, g.usuario["id"], usuario_id)
        except ErroUsuario as erro:
            flash(str(erro), "erro")
            return
        log.info("“%s” excluiu o usuário “%s”", g.usuario["usuario"], alvo.usuario)
        flash("Usuário excluído.", "ok")
    _na_equipe(excluir)
    return redirect(url_for("auth.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/fecha-conta", methods=["POST"])
@login_obrigatorio("admin")
def alternar_fecha_conta(usuario_id):
    """Comanda: autoriza (ou não) um garçom a receber pagamentos e fechar contas."""
    alvo, novo = _na_equipe(lambda contas: contas.alternar_fecha_conta(g.empresa_id, usuario_id))
    log.info("“%s” %s “%s” a fechar contas", g.usuario["usuario"], "autorizou" if novo else "desautorizou", alvo.usuario)
    flash(f"“{alvo.usuario}” {'agora pode' if novo else 'não pode mais'} fechar contas.", "ok")
    return redirect(url_for("auth.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/desativar-2fa", methods=["POST"])
@login_obrigatorio("admin")
def desativar_2fa_usuario(usuario_id):
    """Para quando alguém perde o celular."""
    alvo = _na_equipe(lambda contas: contas.desativar_2fa_de(g.empresa_id, usuario_id))
    log.info("“%s” desativou a 2FA de “%s”", g.usuario["usuario"], alvo.usuario)
    flash(f"Verificação em duas etapas de “{alvo.usuario}” desativada.", "ok")
    return redirect(url_for("auth.usuarios"))
