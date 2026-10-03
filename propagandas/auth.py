"""Login (com 2FA opcional), usuários, permissões, empresas e proteção CSRF."""

import hmac
import logging
import secrets
import threading
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
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, modulos, totp

bp = Blueprint("auth", __name__)
log = logging.getLogger("propagandas.auth")

PAPEIS = {
    "admin": "Administrador",   # tudo da loja: equipe, ajustes, assinatura e os módulos assinados
    "editor": "Editor",         # Painel: propagandas
    "caixa": "Caixa",           # Comanda: comandas, fechamento, cancelamentos e relatórios
    "garcom": "Garçom",         # Comanda: abre comandas e lança pedidos
    "cozinha": "Cozinha",       # Comanda: só a tela da cozinha
}
EMPRESA_PRINCIPAL = 1  # a empresa de quem instalou o sistema
SENHA_MINIMA = 8
MAX_TENTATIVAS = 5
JANELA_BLOQUEIO = 15 * 60  # segundos
VALIDADE_ETAPA_2FA = 5 * 60


class ErroUsuario(ValueError):
    """Dados de usuário inválidos (mensagem pode ser mostrada na tela)."""


# ---------------------------------------------------------------------------
# Regras de usuário (usadas pelo painel, pela plataforma e pelo gerenciar.py)
# ---------------------------------------------------------------------------

def validar_senha(senha):
    if len(senha) < SENHA_MINIMA:
        raise ErroUsuario(f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.")


def criar_usuario(conexao, empresa_id, usuario, senha, papel="editor", plataforma=False):
    usuario = usuario.strip()
    if not usuario or len(usuario) > 50:
        raise ErroUsuario("Informe um nome de usuário (até 50 caracteres).")
    if papel not in PAPEIS:
        raise ErroUsuario("Papel inválido.")
    validar_senha(senha)
    if conexao.execute("SELECT 1 FROM empresas WHERE id = ?", (empresa_id,)).fetchone() is None:
        raise ErroUsuario("Empresa não encontrada.")
    # O nome de usuário é único dentro da empresa: cada loja tem os seus ("joao" pode existir em várias).
    if conexao.execute("SELECT 1 FROM usuarios WHERE empresa_id = ? AND usuario = ?", (empresa_id, usuario)).fetchone():
        raise ErroUsuario(f"O usuário “{usuario}” já existe nesta loja. Escolha outro nome.")
    with conexao:
        cursor = conexao.execute(
            "INSERT INTO usuarios (empresa_id, usuario, senha_hash, papel, plataforma, token_sessao) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (empresa_id, usuario, generate_password_hash(senha), papel, 1 if plataforma else 0, secrets.token_hex(16)),
        )
    return cursor.lastrowid


def trocar_senha(conexao, usuario_id, senha_nova):
    validar_senha(senha_nova)
    with conexao:
        # Trocar o token derruba as sessões abertas em outros aparelhos.
        conexao.execute(
            "UPDATE usuarios SET senha_hash = ?, token_sessao = ? WHERE id = ?",
            (generate_password_hash(senha_nova), secrets.token_hex(16), usuario_id),
        )


def desativar_2fa(conexao, usuario_id):
    with conexao:
        conexao.execute(
            "UPDATE usuarios SET totp_segredo = NULL, totp_ultimo = 0, token_sessao = ? WHERE id = ?",
            (secrets.token_hex(16), usuario_id),
        )


def existe_usuario(conexao):
    return conexao.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone() is not None


# ---------------------------------------------------------------------------
# Limite de tentativas (senha e código 2FA), por IP
# ---------------------------------------------------------------------------

_tentativas = {}
_trava_tentativas = threading.Lock()


def _bloqueado(ip):
    agora = time.monotonic()
    with _trava_tentativas:
        recentes = [t for t in _tentativas.get(ip, []) if agora - t < JANELA_BLOQUEIO]
        if recentes:
            _tentativas[ip] = recentes
        else:
            _tentativas.pop(ip, None)
        return len(recentes) >= MAX_TENTATIVAS


def _registrar_falha(ip):
    with _trava_tentativas:
        _tentativas.setdefault(ip, []).append(time.monotonic())


def _limpar_falhas(ip):
    with _trava_tentativas:
        _tentativas.pop(ip, None)


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
    return conexao.execute(
        "SELECT u.*, e.nome AS empresa_nome, e.slug AS empresa_slug, e.ativa AS empresa_ativa, e.motivo_suspensao "
        "FROM usuarios u JOIN empresas e ON e.id = u.empresa_id WHERE u.id = ?",
        (usuario_id,),
    ).fetchone()


def suspensa_por_pagamento(linha):
    """Empresa suspensa por falta de pagamento: entra, mas só vê a página de pagamento."""
    return not linha["empresa_ativa"] and linha["motivo_suspensao"] == "inadimplencia" and not linha["plataforma"]


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
    if valido and not linha["empresa_ativa"] and not linha["plataforma"] and not suspensa_por_pagamento(linha):
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
                if empresa:
                    with conexao:
                        conexao.execute("UPDATE empresas SET nome = ? WHERE id = ?", (empresa, EMPRESA_PRINCIPAL))
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
        if _bloqueado(ip):
            log.warning("Login bloqueado por excesso de tentativas (IP %s)", ip)
            flash("Muitas tentativas erradas. Aguarde 15 minutos e tente de novo.", "erro")
            return render_template("login.html", usuario=usuario, loja=loja), 429

        consulta = ("SELECT u.*, e.ativa AS empresa_ativa, e.motivo_suspensao FROM usuarios u "
                    "JOIN empresas e ON e.id = u.empresa_id WHERE u.usuario = ?")
        if loja:
            candidatos = conexao.execute(consulta + " AND e.slug = ?", (usuario, loja)).fetchall()
        else:
            candidatos = conexao.execute(consulta, (usuario,)).fetchall()
        # O mesmo nome pode existir em várias lojas: vale a que tem esta senha. A senha é conferida
        # antes de pedir o código da loja, para não revelar a quem não sabe a senha que o nome existe.
        senha = request.form.get("senha", "")
        certos = [c for c in candidatos if check_password_hash(c["senha_hash"], senha)]
        if len(certos) > 1:
            flash("Este usuário e senha existem em mais de uma loja. Informe também o código da sua loja.", "erro")
            return render_template("login.html", usuario=usuario, loja=loja, pedir_loja=True), 401
        linha = certos[0] if certos else None
        if linha:
            if not linha["empresa_ativa"] and not linha["plataforma"] and not suspensa_por_pagamento(linha):
                log.warning("Login recusado: empresa suspensa (“%s”, IP %s)", usuario, ip)
                flash("O acesso desta empresa está suspenso. Fale com o suporte.", "erro")
                return render_template("login.html", usuario=usuario, loja=loja), 403
            proximo = request.args.get("proximo")
            if linha["totp_segredo"]:
                # Senha certa, mas ainda falta o código do aplicativo.
                session.clear()
                session["2fa_usuario"] = linha["id"]
                session["2fa_desde"] = time.time()
                session["2fa_proximo"] = proximo or ""
                return redirect(url_for("auth.login_codigo"))
            _limpar_falhas(ip)
            _entrar(linha)
            log.info("Login de “%s” (IP %s)", linha["usuario"], ip)
            return redirect(_destino_seguro(proximo, linha))

        _registrar_falha(ip)
        log.warning("Senha errada para “%s” (IP %s)", usuario, ip)
        flash("Usuário ou senha incorretos.", "erro")
        return render_template("login.html", usuario=usuario, loja=loja, pedir_loja=bool(loja)), 401

    return render_template("login.html", usuario="", loja=loja, pedir_loja=bool(loja))


@bp.route("/entrar/<slug>")
def entrar_na_loja(slug):
    """Endereço de login de cada loja (o código já vem preenchido): bom para o ícone no celular da equipe."""
    if db.obter().execute("SELECT 1 FROM empresas WHERE slug = ?", (slug.lower(),)).fetchone() is None:
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
        if _bloqueado(ip):
            flash("Muitas tentativas erradas. Aguarde 15 minutos e tente de novo.", "erro")
            return render_template("login_codigo.html"), 429
        conexao = db.obter()
        linha = _buscar_usuario(conexao, usuario_id)
        contador = linha and linha["totp_segredo"] and totp.verificar(
            linha["totp_segredo"], request.form.get("codigo", ""), linha["totp_ultimo"]
        )
        if contador:
            with conexao:
                conexao.execute("UPDATE usuarios SET totp_ultimo = ? WHERE id = ?", (contador, usuario_id))
            proximo = session.get("2fa_proximo")
            _limpar_falhas(ip)
            _entrar(linha)
            log.info("Login de “%s” com 2FA (IP %s)", linha["usuario"], ip)
            return redirect(_destino_seguro(proximo, linha))
        _registrar_falha(ip)
        log.warning("Código 2FA errado (usuário id %s, IP %s)", usuario_id, ip)
        flash("Código incorreto ou já usado. Confira o aplicativo e tente de novo.", "erro")
        return render_template("login_codigo.html"), 401

    return render_template("login_codigo.html")


@bp.route("/sair", methods=["POST"])
def sair():
    session.clear()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Minha conta: senha e 2FA
# ---------------------------------------------------------------------------

@bp.route("/conta")
@login_obrigatorio()
def minha_conta():
    segredo = None
    qr = None
    if not g.usuario["totp_segredo"]:
        # Segredo provisório (guardado no banco, não no cookie): só passa a valer
        # depois que o usuário confirma um código gerado pelo aplicativo.
        segredo = g.usuario["totp_pendente"]
        if not segredo:
            segredo = totp.novo_segredo()
            conexao = db.obter()
            with conexao:
                conexao.execute("UPDATE usuarios SET totp_pendente = ? WHERE id = ?", (segredo, g.usuario["id"]))
        qr = totp.qr_code(segredo, g.usuario["usuario"], current_app.config["NOME_PLATAFORMA"])
    return render_template("conta.html", segredo=segredo, qr=qr)


@bp.route("/conta/senha", methods=["POST"])
@login_obrigatorio()
def minha_senha():
    atual = request.form.get("atual", "")
    nova = request.form.get("nova", "")
    if not check_password_hash(g.usuario["senha_hash"], atual):
        flash("A senha atual está incorreta.", "erro")
    elif nova != request.form.get("confirmacao", ""):
        flash("As senhas novas não conferem.", "erro")
    else:
        try:
            trocar_senha(db.obter(), g.usuario["id"], nova)
        except ErroUsuario as erro:
            flash(str(erro), "erro")
        else:
            _entrar(_buscar_usuario(db.obter(), g.usuario["id"]))  # mantém este aparelho conectado
            log.info("“%s” trocou a própria senha", g.usuario["usuario"])
            flash("Senha alterada. Os outros aparelhos foram desconectados.", "ok")
    return redirect(url_for("auth.minha_conta"))


@bp.route("/conta/2fa/ativar", methods=["POST"])
@login_obrigatorio()
def ativar_2fa():
    segredo = g.usuario["totp_pendente"]
    contador = segredo and totp.verificar(segredo, request.form.get("codigo", ""))
    if not contador:
        flash("Código incorreto. Confira se o aplicativo leu o QR code e tente de novo.", "erro")
        return redirect(url_for("auth.minha_conta"))
    conexao = db.obter()
    with conexao:
        conexao.execute(
            "UPDATE usuarios SET totp_segredo = ?, totp_pendente = NULL, totp_ultimo = ?, token_sessao = ? WHERE id = ?",
            (segredo, contador, secrets.token_hex(16), g.usuario["id"]),
        )
    _entrar(_buscar_usuario(conexao, g.usuario["id"]))
    log.info("“%s” ativou a verificação em duas etapas", g.usuario["usuario"])
    flash("Verificação em duas etapas ativada. Os outros aparelhos foram desconectados.", "ok")
    return redirect(url_for("auth.minha_conta"))


@bp.route("/conta/2fa/desativar", methods=["POST"])
@login_obrigatorio()
def desativar_2fa_proprio():
    if not check_password_hash(g.usuario["senha_hash"], request.form.get("senha", "")):
        flash("Senha incorreta.", "erro")
    elif not totp.verificar(g.usuario["totp_segredo"] or "", request.form.get("codigo", ""), g.usuario["totp_ultimo"]):
        flash("Código incorreto.", "erro")
    else:
        desativar_2fa(db.obter(), g.usuario["id"])
        _entrar(_buscar_usuario(db.obter(), g.usuario["id"]))
        log.info("“%s” desativou a verificação em duas etapas", g.usuario["usuario"])
        flash("Verificação em duas etapas desativada.", "ok")
    return redirect(url_for("auth.minha_conta"))


# ---------------------------------------------------------------------------
# Usuários da empresa (administradores)
# ---------------------------------------------------------------------------

def _usuario_da_empresa(conexao, usuario_id):
    alvo = conexao.execute(
        "SELECT * FROM usuarios WHERE id = ? AND empresa_id = ?", (usuario_id, g.empresa_id)
    ).fetchone()
    if alvo is None:
        abort(404)
    return alvo


@bp.route("/usuarios")
@login_obrigatorio("admin")
def usuarios():
    linhas = db.obter().execute(
        "SELECT * FROM usuarios WHERE empresa_id = ? ORDER BY usuario", (g.empresa_id,)
    ).fetchall()
    return render_template("usuarios.html", usuarios=linhas)


def _papel_permitido(papel):
    """Só papéis dos módulos que a loja tem (admin sempre); papel desconhecido cai no criar_usuario."""
    if papel == "admin" or papel not in PAPEIS:
        return papel
    if any(papel in modulos.PAPEIS_DO_MODULO[m] for m in g.modulos_empresa):
        return papel
    raise ErroUsuario("Este papel é de um módulo que sua loja não assinou.")


@bp.route("/usuarios/novo", methods=["POST"])
@login_obrigatorio("admin")
def novo_usuario():
    try:
        criar_usuario(
            db.obter(),
            g.empresa_id,
            request.form.get("usuario", ""),
            request.form.get("senha", ""),
            _papel_permitido(request.form.get("papel", "editor")),
        )
    except ErroUsuario as erro:
        flash(str(erro), "erro")
    else:
        log.info("“%s” criou o usuário “%s”", g.usuario["usuario"], request.form.get("usuario", "").strip())
        flash("Usuário criado.", "ok")
    return redirect(url_for("auth.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/excluir", methods=["POST"])
@login_obrigatorio("admin")
def excluir_usuario(usuario_id):
    conexao = db.obter()
    alvo = _usuario_da_empresa(conexao, usuario_id)
    if alvo["id"] == g.usuario["id"]:
        flash("Você não pode excluir o próprio usuário.", "erro")
    else:
        with conexao:
            conexao.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
        log.info("“%s” excluiu o usuário “%s”", g.usuario["usuario"], alvo["usuario"])
        flash("Usuário excluído.", "ok")
    return redirect(url_for("auth.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/fecha-conta", methods=["POST"])
@login_obrigatorio("admin")
def alternar_fecha_conta(usuario_id):
    """Comanda: autoriza (ou não) um garçom a receber pagamentos e fechar contas."""
    conexao = db.obter()
    alvo = _usuario_da_empresa(conexao, usuario_id)
    if alvo["papel"] != "garcom":
        abort(400)
    novo = 0 if alvo["fecha_conta"] else 1
    with conexao:
        conexao.execute("UPDATE usuarios SET fecha_conta = ? WHERE id = ?", (novo, usuario_id))
    log.info("“%s” %s “%s” a fechar contas", g.usuario["usuario"], "autorizou" if novo else "desautorizou", alvo["usuario"])
    flash(f"“{alvo['usuario']}” {'agora pode' if novo else 'não pode mais'} fechar contas.", "ok")
    return redirect(url_for("auth.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/desativar-2fa", methods=["POST"])
@login_obrigatorio("admin")
def desativar_2fa_usuario(usuario_id):
    """Para quando alguém perde o celular."""
    conexao = db.obter()
    alvo = _usuario_da_empresa(conexao, usuario_id)
    desativar_2fa(conexao, usuario_id)
    log.info("“%s” desativou a 2FA de “%s”", g.usuario["usuario"], alvo["usuario"])
    flash(f"Verificação em duas etapas de “{alvo['usuario']}” desativada.", "ok")
    return redirect(url_for("auth.usuarios"))
