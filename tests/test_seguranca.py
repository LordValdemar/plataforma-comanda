import os
import re
from datetime import datetime, timedelta, timezone

from conftest import configurar_admin, csrf, postar
from propagandas import auth


def test_primeiro_acesso_pede_para_criar_admin(cliente):
    assert cliente.get("/").headers["Location"].endswith("/configurar")
    resposta = configurar_admin(cliente)
    assert resposta.headers["Location"].endswith("/")
    assert cliente.get("/").status_code == 200
    # Depois de configurado, a página de configuração some.
    assert cliente.get("/configurar").headers["Location"].endswith("/login")


def test_painel_exige_login_mas_exibicao_e_publica(cliente):
    configurar_admin(cliente)
    anonimo = cliente.application.test_client()
    assert "/login" in anonimo.get("/").headers["Location"]
    assert anonimo.get("/usuarios").status_code == 302
    assert anonimo.get("/player").status_code == 200
    assert anonimo.get("/api/playlist").status_code == 200
    assert anonimo.get("/saude").get_json() == {"status": "ok"}


def test_post_sem_csrf_e_recusado(logado):
    assert logado.post("/letreiro", data={"letreiro": "x"}).status_code == 400
    assert logado.post("/letreiro", data={"letreiro": "x", "csrf_token": "falso"}).status_code == 400
    assert postar(logado, "/letreiro", {"letreiro": "ok"}).status_code == 302


def test_login_e_bloqueio_por_tentativas(cliente):
    configurar_admin(cliente)
    novo = cliente.application.test_client()
    token = csrf(novo)
    assert novo.post("/login", data={"usuario": "admin", "senha": "errada", "csrf_token": token}).status_code == 401
    for _ in range(auth.MAX_TENTATIVAS - 1):
        novo.post("/login", data={"usuario": "admin", "senha": "errada", "csrf_token": token})
    # Mesmo com a senha certa, fica bloqueado depois de muitas tentativas.
    bloqueado = novo.post("/login", data={"usuario": "admin", "senha": "senha-forte-123", "csrf_token": token})
    assert bloqueado.status_code == 429

    auth._tentativas.clear()
    ok = novo.post("/login?proximo=/usuarios", data={"usuario": "ADMIN", "senha": "senha-forte-123", "csrf_token": token})
    assert ok.headers["Location"].endswith("/usuarios")


def test_login_nao_redireciona_para_outro_site(cliente):
    configurar_admin(cliente)
    novo = cliente.application.test_client()
    resposta = novo.post(
        "/login?proximo=//site-malicioso.com",
        data={"usuario": "admin", "senha": "senha-forte-123", "csrf_token": csrf(novo)},
    )
    assert resposta.headers["Location"] == "/"


def test_editor_nao_gerencia_usuarios(logado):
    postar(logado, "/usuarios/novo", {"usuario": "maria", "senha": "senha-da-maria", "papel": "editor"}, pagina="/usuarios")
    maria = logado.application.test_client()
    maria.post("/login", data={"usuario": "maria", "senha": "senha-da-maria", "csrf_token": csrf(maria)})
    assert maria.get("/").status_code == 200
    assert maria.get("/usuarios").status_code == 403


def test_trocar_senha_derruba_outras_sessoes(logado):
    outro_aparelho = logado.application.test_client()
    outro_aparelho.post("/login", data={"usuario": "admin", "senha": "senha-forte-123", "csrf_token": csrf(outro_aparelho)})
    assert outro_aparelho.get("/").status_code == 200

    resposta = postar(
        logado, "/conta/senha",
        {"atual": "senha-forte-123", "nova": "outra-senha-456", "confirmacao": "outra-senha-456"},
        pagina="/conta",
    )
    assert resposta.status_code == 302
    assert logado.get("/").status_code == 200           # este continua conectado
    assert outro_aparelho.get("/").status_code == 302   # o outro foi desconectado


def test_senha_curta_e_recusada(cliente):
    resposta = postar(cliente, "/configurar", {"usuario": "a", "senha": "123", "confirmacao": "123"}, pagina="/configurar")
    assert "pelo menos 8" in resposta.get_data(as_text=True)


def test_cabecalhos_e_cookie_de_sessao(cliente):
    cookie = configurar_admin(cliente).headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie

    resposta = cliente.get("/")
    assert "script-src 'self'" in resposta.headers["Content-Security-Policy"]
    assert resposta.headers["X-Frame-Options"] == "DENY"
    permissoes = resposta.headers["Permissions-Policy"]
    assert "camera=()" in permissoes and "screen-wake-lock=(self)" in permissoes
    assert resposta.headers["Cache-Control"] == "no-store"


def test_security_txt(app):
    cliente = app.test_client()
    assert cliente.get("/.well-known/security.txt").status_code == 404  # sem contato configurado
    app.config["CONTATO_PLATAFORMA"] = "seguranca@exemplo.com.br"
    resposta = cliente.get("/.well-known/security.txt", base_url="https://loja.exemplo.com.br")
    texto = resposta.get_data(as_text=True)
    assert resposta.status_code == 200 and resposta.mimetype == "text/plain"
    assert "Contact: mailto:seguranca@exemplo.com.br" in texto
    assert "Canonical: https://loja.exemplo.com.br/.well-known/security.txt" in texto
    validade = re.search(r"Expires: (\S+)", texto).group(1)
    assert datetime.fromisoformat(validade.replace("Z", "+00:00")) > datetime.now(timezone.utc) + timedelta(days=150)


def test_csp_sem_estilo_embutido(logado):
    csp = logado.get("/").headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "style-src 'self'" in csp
    # Nenhuma página do sistema pode depender de estilo embutido (seria bloqueado pela CSP).
    pasta = os.path.join(os.path.dirname(__file__), "..", "propagandas", "templates")
    for raiz, _, arquivos in os.walk(pasta):
        for nome in arquivos:
            with open(os.path.join(raiz, nome), encoding="utf-8") as arquivo:
                html = arquivo.read()
            assert 'style="' not in html and "<style" not in html, nome
