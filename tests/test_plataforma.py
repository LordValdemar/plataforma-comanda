"""Plataforma de assinatura: Comanda como módulo, isolamento entre lojas, módulos por plano,
login com o código da loja, cadastro aberto e assinatura pelo próprio cliente."""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_cobranca import AsaasFalso

from conftest import csrf, postar
from propagandas import asaas, db


def consultar(cliente, sql, *parametros):
    with cliente.application.app_context():
        return db.obter().execute(sql, parametros).fetchall()


def criar_loja(plataforma, nome, usuario, modulos=("comanda",), senha="senha-da-loja"):
    postar(plataforma, "/plataforma/empresas/nova",
           {"nome": nome, "usuario": usuario, "senha": senha, "modulos_enviados": "1", "modulos": list(modulos)},
           pagina="/plataforma/")
    return consultar(plataforma, "SELECT * FROM empresas WHERE nome = ?", nome)[0]


def entrar(app, usuario, senha="senha-da-loja", loja=""):
    cliente = app.test_client()
    resposta = cliente.post("/login", data={"usuario": usuario, "senha": senha, "loja": loja, "csrf_token": csrf(cliente)})
    cliente.ultima_resposta = resposta
    return cliente


def criar_usuario_na_loja(admin, usuario, papel, senha="senha-da-loja"):
    postar(admin, "/usuarios/novo", {"usuario": usuario, "senha": senha, "papel": papel}, pagina="/usuarios")


def criar_produto(admin, nome, preco, cozinha=True, codigo=""):
    dados = {"nome": nome, "preco": preco, "codigo": codigo}
    if cozinha:
        dados["vai_cozinha"] = "on"
    postar(admin, "/comanda/cardapio/produtos", dados, pagina="/comanda/cardapio/")
    return consultar(admin, "SELECT id FROM cmd_produtos WHERE nome = ? ORDER BY id DESC", nome)[0]["id"]


def abrir_comanda(cliente, numero, mesa=""):
    resposta = postar(cliente, "/comanda/", {"numero": str(numero), "mesa": mesa}, pagina="/comanda/")
    return int(resposta.headers["Location"].rstrip("/").split("/")[-1])


def token_cozinha(cliente):
    return re.search(r'data-csrf="([^"]+)"', cliente.get("/comanda/cozinha").get_data(as_text=True)).group(1)


@pytest.fixture
def lanchonete(logado):
    """Plataforma (logado) e uma lanchonete com a Comanda, entrando com o administrador dela."""
    loja = criar_loja(logado, "Padeiro Lanches", "dono")
    return logado, entrar(logado.application, "dono"), loja


# ---------------------------------------------------------------------------
# Comanda dentro da plataforma
# ---------------------------------------------------------------------------

def test_fluxo_completo_da_comanda(lanchonete):
    _, dono, _ = lanchonete
    lanche = criar_produto(dono, "X-Burger", "25,00")
    lata = criar_produto(dono, "Refri lata", "6", cozinha=False)
    criar_usuario_na_loja(dono, "joao", "garcom")
    criar_usuario_na_loja(dono, "chef", "cozinha")
    app = dono.application

    garcom = entrar(app, "joao")
    assert garcom.ultima_resposta.headers["Location"].endswith("/comanda/")
    comanda_id = abrir_comanda(garcom, 7, mesa="3")
    postar(garcom, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "2", f"qtd_{lata}": "1"}, pagina="/comanda/")
    assert "R$ 56,00" in garcom.get(f"/comanda/{comanda_id}").get_data(as_text=True)

    cozinha = entrar(app, "chef")
    assert cozinha.ultima_resposta.headers["Location"].endswith("/comanda/cozinha")
    dados = cozinha.get("/comanda/api/cozinha").get_json()
    item = dados["comandas"][0]["itens"][0]
    assert item["nome"] == "X-Burger"  # a lata não passa pela cozinha
    cabecalho = {"X-CSRF-Token": token_cozinha(cozinha)}
    assert cozinha.post(f"/comanda/api/cozinha/itens/{item['id']}", data={"status": "pronto"}, headers=cabecalho).get_json() == {"status": "pronto"}
    assert "Prontos para servir" in garcom.get("/comanda/").get_data(as_text=True)
    # A cozinha não fecha conta nem abre o cardápio.
    assert cozinha.get(f"/comanda/{comanda_id}/fechar").status_code == 403
    assert cozinha.get("/comanda/cardapio/").status_code == 403

    postar(dono, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "pix", "valor": "61,60"}, pagina="/comanda/")
    resposta = postar(dono, f"/comanda/{comanda_id}/fechar", {"acao": "finalizar"}, pagina="/comanda/")
    assert "/cupom" in resposta.headers["Location"]
    cupom = dono.get(f"/comanda/{comanda_id}/cupom").get_data(as_text=True)
    assert "Padeiro Lanches" in cupom and "R$ 61,60" in cupom
    assert "R$ 61,60" in dono.get("/comanda/relatorios/").get_data(as_text=True)


def test_lojas_nao_veem_nem_mexem_na_comanda_da_outra(logado):
    loja_a = criar_loja(logado, "Loja A", "dono")
    criar_loja(logado, "Loja B", "outro")
    a = entrar(logado.application, "dono")
    b = entrar(logado.application, "outro")
    produto = criar_produto(a, "Segredo da A", "10", codigo="7")
    comanda_id = abrir_comanda(a, 5)
    postar(a, f"/comanda/{comanda_id}/itens", {f"qtd_{produto}": "1"}, pagina="/comanda/")
    item_id = consultar(a, "SELECT id FROM cmd_itens")[0]["id"]
    postar(a, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "pix", "valor": "11"}, pagina="/comanda/")

    for pagina in ("/comanda/", "/comanda/cardapio/", "/comanda/relatorios/", "/comanda/historico"):
        assert "Segredo da A" not in b.get(pagina).get_data(as_text=True), pagina
    assert b.get("/comanda/api/cozinha").get_json()["comandas"] == []
    for url in (f"/comanda/{comanda_id}", f"/comanda/{comanda_id}/cupom", f"/comanda/{comanda_id}/fechar"):
        assert b.get(url).status_code == 404, url
    for url, dados in (
        (f"/comanda/{comanda_id}/itens", {f"qtd_{produto}": "5"}),
        (f"/comanda/{comanda_id}/fechar", {"acao": "finalizar"}),
        (f"/comanda/{comanda_id}/cancelar", {"motivo": "invasão"}),
        (f"/comanda/cardapio/produtos/{produto}", {"acao": "excluir"}),
    ):
        assert postar(b, url, dados, pagina="/comanda/").status_code == 404, url
    assert b.post(f"/comanda/api/cozinha/itens/{item_id}", data={"status": "entregue"},
                  headers={"X-CSRF-Token": token_cozinha(b)}).status_code == 404

    # A loja B não lança o produto da A na comanda dela, nem pelo código.
    da_b = abrir_comanda(b, 5)  # o mesmo número pode estar aberto em lojas diferentes
    postar(b, f"/comanda/{da_b}/itens", {f"qtd_{produto}": "1"}, pagina="/comanda/")
    postar(b, f"/comanda/{da_b}/itens", {"codigo": "7"}, pagina="/comanda/")
    assert consultar(a, "SELECT COUNT(*) FROM cmd_itens WHERE comanda_id = ?", da_b)[0][0] == 0
    assert consultar(a, "SELECT status FROM cmd_comandas WHERE id = ?", comanda_id)[0]["status"] == "aberta"
    assert consultar(a, "SELECT empresa_id FROM cmd_comandas WHERE id = ?", comanda_id)[0]["empresa_id"] == loja_a["id"]


# ---------------------------------------------------------------------------
# Módulos por plano e papéis
# ---------------------------------------------------------------------------

def test_loja_so_com_painel_ve_convite_para_assinar_a_comanda(logado):
    criar_loja(logado, "Só Painel", "tv", modulos=("painel",))
    tv = entrar(logado.application, "tv")
    resposta = tv.get("/comanda/")
    assert resposta.status_code == 402
    assert "Ver planos e assinar" in resposta.get_data(as_text=True)
    assert tv.get("/comanda/api/cozinha").status_code == 403
    assert "Comandas" not in tv.get("/").get_data(as_text=True)  # o menu não mostra o módulo


def test_loja_so_com_comanda_nao_abre_o_painel(lanchonete):
    _, dono, _ = lanchonete
    assert dono.get("/").headers["Location"].endswith("/comanda/")
    assert dono.get("/telas").status_code == 402
    assert dono.get("/relatorios").status_code == 402


def test_papeis_de_cada_modulo(logado):
    criar_loja(logado, "Completa", "dono", modulos=("painel", "comanda"))
    dono = entrar(logado.application, "dono")
    criar_usuario_na_loja(dono, "joao", "garcom")
    criar_usuario_na_loja(dono, "ana", "editor")
    garcom = entrar(logado.application, "joao")
    editor = entrar(logado.application, "ana")
    assert garcom.get("/comanda/").status_code == 200
    assert garcom.get("/").headers["Location"].endswith("/comanda/")
    assert garcom.get("/relatorios").status_code == 403   # papel da Comanda não usa o Painel
    assert editor.get("/").status_code == 200
    assert editor.get("/comanda/").status_code == 403      # papel do Painel não usa a Comanda


def test_papel_de_modulo_nao_assinado_e_recusado(lanchonete):
    _, dono, _ = lanchonete
    criar_usuario_na_loja(dono, "ana", "editor")  # a loja não tem o Painel
    assert consultar(dono, "SELECT * FROM usuarios WHERE usuario = 'ana'") == []


# ---------------------------------------------------------------------------
# Login com o código da loja
# ---------------------------------------------------------------------------

def test_mesmo_usuario_em_duas_lojas(logado):
    a = criar_loja(logado, "Lanchonete A", "joao", senha="senha-da-loja-a")
    b = criar_loja(logado, "Lanchonete B", "joao", senha="senha-da-loja-b")
    criar_loja(logado, "Lanchonete C", "joao", senha="senha-da-loja-a")  # mesma senha da A
    assert a["slug"] == "lanchonete-a" and b["slug"] == "lanchonete-b"

    # A senha decide a loja: só a B tem esta senha, então entra direto na B.
    direto = entrar(logado.application, "joao", "senha-da-loja-b")
    assert direto.ultima_resposta.status_code == 302
    inicio = direto.get("/loja").get_data(as_text=True)
    assert "<h2>Lanchonete B</h2>" in inicio and "/entrar/lanchonete-b" in inicio

    # Mesma senha em duas lojas (A e C): aí precisa do código.
    ambiguo = entrar(logado.application, "joao", "senha-da-loja-a")
    assert ambiguo.ultima_resposta.status_code == 401
    assert "código da sua loja" in ambiguo.ultima_resposta.get_data(as_text=True)
    com_codigo = entrar(logado.application, "joao", "senha-da-loja-a", loja="lanchonete-c")
    assert "<h2>Lanchonete C</h2>" in com_codigo.get("/loja").get_data(as_text=True)

    # Senha errada não revela que o nome existe em várias lojas, e senha de uma loja não abre a outra.
    errada = entrar(logado.application, "joao", "senha-que-ninguem-tem")
    assert errada.ultima_resposta.status_code == 401
    assert "código da sua loja" not in errada.ultima_resposta.get_data(as_text=True)
    assert entrar(logado.application, "joao", "senha-da-loja-a", loja="lanchonete-b").ultima_resposta.status_code == 401


def test_endereco_de_entrada_da_loja(lanchonete):
    _, _, loja = lanchonete
    cliente = lanchonete[0].application.test_client()
    assert cliente.get(f"/entrar/{loja['slug']}").headers["Location"].endswith(f"/login?loja={loja['slug']}")
    assert f'value="{loja["slug"]}"' in cliente.get(f"/login?loja={loja['slug']}").get_data(as_text=True)
    assert cliente.get("/entrar/nao-existe").status_code == 404


# ---------------------------------------------------------------------------
# Cadastro aberto e assinatura pelo cliente
# ---------------------------------------------------------------------------

@pytest.fixture
def asaas_falso(app, monkeypatch):
    app.config.update(ASAAS_API_KEY="$aact_hmlg_teste", ASAAS_WEBHOOK_TOKEN="x" * 40, CADASTRO_ABERTO=True)
    falso = AsaasFalso()
    monkeypatch.setattr(asaas, "_enviar", falso)
    return falso


def criar_planos(plataforma):
    for nome, preco, mods in (("Comanda", "79,90", ["comanda"]), ("Completo", "119,90", ["painel", "comanda"])):
        postar(plataforma, "/plataforma/planos/novo",
               {"nome": nome, "preco": preco, "modulos_enviados": "1", "modulos": mods}, pagina="/plataforma/")
    return {p["nome"]: p["id"] for p in consultar(plataforma, "SELECT * FROM planos")}


def cadastrar(app, loja="Padeiro Lanches", usuario="dono", plano=""):
    cliente = app.test_client()
    resposta = cliente.post("/cadastro", data={
        "loja": loja, "codigo": "", "email": "dono@padeiro.com", "usuario": usuario,
        "senha": "senha-forte-1", "confirmacao": "senha-forte-1", "termos": "on", "plano": plano,
        "csrf_token": csrf(cliente, "/cadastro"),
    })
    return cliente, resposta


def test_cadastro_assinatura_troca_e_cancelamento(logado, asaas_falso):
    planos = criar_planos(logado)
    anonimo = logado.application.test_client()
    publica = anonimo.get("/").get_data(as_text=True)
    assert "Criar conta da loja" in publica and "R$ 79,90" in publica and "R$ 119,90" in publica

    dono, resposta = cadastrar(logado.application, plano=str(planos["Comanda"]))
    assert resposta.headers["Location"].endswith(f"/loja/assinar/{planos['Comanda']}")
    loja = consultar(logado, "SELECT * FROM empresas WHERE nome = 'Padeiro Lanches'")[0]
    assert loja["slug"] == "padeiro-lanches" and loja["plano_id"] is None
    assert dono.get("/comanda/").status_code == 402  # ainda sem plano

    # CPF/CNPJ inválido não assina.
    postar(dono, f"/loja/assinar/{planos['Comanda']}", {"documento": "123", "email": "dono@padeiro.com"}, pagina="/loja")
    assert consultar(logado, "SELECT plano_id FROM empresas WHERE id = ?", loja["id"])[0]["plano_id"] is None

    postar(dono, f"/loja/assinar/{planos['Comanda']}", {"documento": "11.222.333/0001-81", "email": "dono@padeiro.com"},
           pagina="/loja")
    loja = consultar(logado, "SELECT * FROM empresas WHERE id = ?", loja["id"])[0]
    assert (loja["plano_id"], loja["asaas_assinatura_id"], loja["cobranca_automatica"]) == (planos["Comanda"], "sub_000001", 1)
    assinatura = next(corpo for metodo, caminho, corpo in asaas_falso.chamadas if caminho == "/subscriptions")
    assert assinatura["value"] == 79.9
    hoje_no_brasil = datetime.now(ZoneInfo("America/Sao_Paulo")).date()  # o relógio do servidor fica em UTC
    assert assinatura["nextDueDate"] == (hoje_no_brasil + timedelta(days=7)).isoformat()  # teste grátis
    assert dono.get("/comanda/").status_code == 200
    assert dono.get("/telas").status_code == 402  # o plano Comanda não tem o Painel

    # Troca para o Completo: o Asaas muda o valor e o Painel libera.
    postar(dono, f"/loja/assinar/{planos['Completo']}", {}, pagina="/loja")
    assert ("PUT", "/subscriptions/sub_000001") in [(m, c) for m, c, _ in asaas_falso.chamadas]
    assert dono.get("/telas").status_code == 200

    # Cancelamento: a assinatura sai do Asaas e os módulos travam; os dados ficam.
    criar_produto(dono, "X-Salada", "20")
    postar(dono, "/loja/cancelar", {}, pagina="/loja")
    assert ("DELETE", "/subscriptions/sub_000001") in [(m, c) for m, c, _ in asaas_falso.chamadas]
    assert dono.get("/comanda/").status_code == 402
    assert consultar(logado, "SELECT COUNT(*) FROM cmd_produtos WHERE empresa_id = ?", loja["id"])[0][0] == 1


def test_cadastro_com_codigo_repetido_ou_dados_errados(logado, asaas_falso):
    cadastrar(logado.application, loja="Padeiro Lanches")
    cliente = logado.application.test_client()
    resposta = cliente.post("/cadastro", data={
        "loja": "Outra", "codigo": "padeiro-lanches", "email": "x@y.com", "usuario": "a", "senha": "senha-forte-1",
        "confirmacao": "senha-forte-1", "termos": "on", "csrf_token": csrf(cliente, "/cadastro"),
    })
    assert resposta.status_code == 400 and "já é de outra loja" in resposta.get_data(as_text=True)
    resposta = cliente.post("/cadastro", data={
        "loja": "Outra", "email": "x@y.com", "usuario": "a", "senha": "senha-forte-1", "confirmacao": "senha-forte-1",
        "csrf_token": csrf(cliente, "/cadastro"),
    })
    assert "aceite os Termos" in resposta.get_data(as_text=True)
    assert len(consultar(logado, "SELECT * FROM empresas WHERE nome = 'Outra'")) == 0


def test_cadastro_fechado_por_padrao(logado):
    assert logado.application.test_client().get("/cadastro").status_code == 404
    assert logado.application.test_client().get("/").headers["Location"].startswith("/login")


def test_assinatura_sem_asaas_configurado(logado):
    logado.application.config["CADASTRO_ABERTO"] = True
    planos = criar_planos(logado)
    dono, _ = cadastrar(logado.application)
    resposta = postar(dono, f"/loja/assinar/{planos['Comanda']}", {"documento": "11222333000181", "email": "a@b.com"},
                      pagina="/loja", follow_redirects=True)
    assert "Fale com o suporte" in resposta.get_data(as_text=True)
    assert consultar(logado, "SELECT plano_id FROM empresas WHERE nome = 'Padeiro Lanches'")[0]["plano_id"] is None


def test_garcom_nao_assina_nem_cancela(lanchonete, asaas_falso):
    plataforma, dono, _ = lanchonete
    planos = criar_planos(plataforma)
    criar_usuario_na_loja(dono, "joao", "garcom")
    garcom = entrar(plataforma.application, "joao")
    assert postar(garcom, f"/loja/assinar/{planos['Comanda']}", {}, pagina="/comanda/").status_code == 403
    assert postar(garcom, "/loja/cancelar", {}, pagina="/comanda/").status_code == 403


def test_banco_antigo_do_painel_continua_funcionando(tmp_path):
    """Um banco da versão 4 (antes da plataforma) ganha códigos de loja, mantém o Painel e os logins."""
    import sqlite3

    from werkzeug.security import generate_password_hash

    from propagandas import create_app
    from propagandas.db import MIGRACOES

    pasta = tmp_path / "dados"
    pasta.mkdir()
    conexao = sqlite3.connect(pasta / "banco.sqlite3")
    for numero, migracao in enumerate(MIGRACOES[:4], start=1):
        conexao.executescript(f"BEGIN;\n{migracao}\nPRAGMA user_version = {numero};\nCOMMIT;")
    conexao.execute("UPDATE empresas SET nome = 'Loja Antiga' WHERE id = 1")
    conexao.execute("INSERT INTO usuarios (empresa_id, usuario, senha_hash, papel, plataforma, token_sessao) "
                    "VALUES (1, 'velho', ?, 'admin', 1, 't')", (generate_password_hash("senha-antiga-1"),))
    conexao.execute("INSERT INTO empresas (id, nome) VALUES (2, 'Cliente Antigo')")
    conexao.execute("INSERT INTO usuarios (empresa_id, usuario, senha_hash, papel, token_sessao) "
                    "VALUES (2, 'cliente', ?, 'editor', 't')", (generate_password_hash("senha-antiga-2"),))
    conexao.commit()
    conexao.close()

    app = create_app({"PASTA_DADOS": str(pasta), "TESTING": True, "SECRET_KEY": "teste"})
    linhas = {e["nome"]: e for e in consultar(app.test_client(), "SELECT * FROM empresas")}
    assert linhas["Loja Antiga"]["slug"] == "loja-antiga"
    assert linhas["Cliente Antigo"]["slug"] == "cliente-antigo"
    assert linhas["Cliente Antigo"]["modulos_liberados"] == "painel"  # quem já usava continua com o Painel
    cliente = entrar(app, "cliente", "senha-antiga-2")
    assert cliente.ultima_resposta.status_code == 302
    assert cliente.get("/").status_code == 200
