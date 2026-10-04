"""Módulos da plataforma (Painel de Propagandas e Comanda): quem pode usar o quê.

Cada empresa usa os módulos do plano que assinou, mais os que a plataforma liberou à mão
(``empresas.modulos_liberados``). A empresa principal (de quem opera a plataforma) usa todos.
Dentro da empresa, o papel do usuário decide a que módulo ele tem acesso.
"""

from flask import abort, g, redirect, render_template, request, url_for

from src.domain.permissoes import PAPEIS_DO_MODULO, modulos_da_pessoa  # noqa: F401 (papéis de cada módulo)

from . import db

MODULOS = {
    "painel": "Painel de Propagandas",
    "comanda": "Comanda",
}
DESCRICOES = {
    "painel": "Propagandas nas TVs da loja, com agendamento e relatórios de exibição.",
    "comanda": "Pedidos pelo celular dos garçons, tela da cozinha, fechamento de conta e relatórios de vendas.",
}


def ler(texto):
    """'painel,comanda' → {'painel', 'comanda'} (ignora nomes desconhecidos)."""
    return {m.strip() for m in (texto or "").split(",") if m.strip() in MODULOS}


def juntar(modulos):
    return ",".join(m for m in MODULOS if m in modulos)


def do_formulario(form, padrao="painel"):
    """Caixas "modulos" de um formulário. Sem o campo-marca (formulário antigo), vale o padrão."""
    if form.get("modulos_enviados") != "1":
        return padrao
    return juntar(set(form.getlist("modulos")))


def da_empresa(conexao, empresa_id):
    from .auth import EMPRESA_PRINCIPAL  # evita importação circular

    if empresa_id == EMPRESA_PRINCIPAL:
        return set(MODULOS)
    linha = conexao.execute(
        "SELECT e.modulos_liberados, p.modulos AS do_plano FROM empresas e "
        "LEFT JOIN planos p ON p.id = e.plano_id WHERE e.id = ?",
        (empresa_id,),
    ).fetchone()
    if linha is None:
        return set()
    return ler(linha["modulos_liberados"]) | ler(linha["do_plano"])


def do_usuario(usuario, modulos_empresa):
    """Módulos que este usuário abre: os da empresa que combinam com o papel dele."""
    return set(modulos_da_pessoa(usuario["papel"], bool(usuario["plataforma"]), modulos_empresa))


def carregar():
    """before_request: g.modulos_empresa e g.modulos (do usuário logado)."""
    g.modulos_empresa = set()
    g.modulos = set()
    if getattr(g, "usuario", None) is None:
        return
    g.modulos_empresa = da_empresa(db.obter(), g.empresa_id)
    g.modulos = do_usuario(g.usuario, g.modulos_empresa)


def exigir(modulo):
    """before_request de um blueprint: só entra quem tem o módulo (na empresa e no papel)."""
    def verificar():
        if getattr(g, "usuario", None) is None or request.endpoint is None:
            return None  # o login_obrigatorio da rota manda para o login
        if modulo in g.modulos:
            return None
        if request.endpoint == "painel.lista":
            return redirect(pagina_inicial())  # "/" leva cada pessoa para o módulo que ela usa
        if modulo not in g.modulos_empresa:
            if "/api/" in request.path or request.method != "GET":
                abort(403)
            # A loja não assinou: mostra a página do módulo com o convite para assinar.
            return render_template("modulo_bloqueado.html", modulo=modulo, nome=MODULOS[modulo],
                                   descricao=DESCRICOES[modulo]), 402
        abort(403)  # a loja tem o módulo, mas o papel desta pessoa não usa ele

    return verificar


def pagina_inicial(usuario=None, modulos=None):
    """Para onde cada pessoa vai depois do login."""
    usuario = usuario if usuario is not None else g.usuario
    modulos = modulos if modulos is not None else g.modulos
    if usuario["papel"] == "cozinha" and "comanda" in modulos:
        return url_for("comanda_cozinha.tela")
    if usuario["papel"] in ("garcom", "caixa") and "comanda" in modulos:
        return url_for("comanda.lista")
    if usuario["papel"] in ("admin", "editor") and "painel" in modulos:
        return url_for("painel.lista")
    if usuario["papel"] == "admin" and "comanda" in modulos:
        return url_for("comanda.lista")
    return url_for("conta.inicio")

