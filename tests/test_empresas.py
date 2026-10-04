import io
import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timezone

import pytest

from conftest import PNG, conectar_tv, csrf, enviar, postar
from propagandas import alertas, create_app, db, totp
from propagandas.db import MIGRACOES


def consultar(cliente, sql, *parametros):
    with cliente.application.app_context():
        return db.obter().execute(sql, parametros).fetchall()


def criar_empresa(plataforma, nome, usuario, senha="senha-do-cliente", limite_telas="", limite_mb=""):
    postar(plataforma, "/plataforma/empresas/nova",
           {"nome": nome, "usuario": usuario, "senha": senha, "limite_telas": limite_telas, "limite_mb": limite_mb},
           pagina="/plataforma/")
    return consultar(plataforma, "SELECT id FROM empresas WHERE nome = ?", nome)[0]["id"]


def entrar(app, usuario, senha="senha-do-cliente"):
    cliente = app.test_client()
    cliente.post("/login", data={"usuario": usuario, "senha": senha, "csrf_token": csrf(cliente)})
    return cliente


def criar_tela(cliente, nome):
    """Cadastra a tela e conecta o próprio cliente do teste como a TV dela."""
    postar(cliente, "/telas/nova", {"nome": nome}, pagina="/telas")
    tela = consultar(cliente, "SELECT * FROM telas WHERE nome = ?", nome)[0]
    conectar_tv(cliente, tela["id"])
    return tela


@pytest.fixture
def duas_empresas(logado):
    """Empresa principal (logado, administrador da plataforma) e uma empresa cliente."""
    cliente_id = criar_empresa(logado, "Padaria Cliente", "joao")
    joao = entrar(logado.application, "joao")
    return logado, joao, cliente_id


# ---------------------------------------------------------------------------
# Isolamento entre empresas
# ---------------------------------------------------------------------------

def test_cliente_nao_ve_nem_altera_dados_de_outra_empresa(duas_empresas):
    dono, joao, _ = duas_empresas
    enviar(dono, "segredo-do-dono.png", PNG)
    pid = consultar(dono, "SELECT id FROM propagandas")[0]["id"]
    tela = criar_tela(dono, "Tela do dono")
    postar(dono, "/grupos/novo", {"nome": "Grupo do dono"}, pagina="/telas")
    gid = consultar(dono, "SELECT id FROM grupos")[0]["id"]
    uid = consultar(dono, "SELECT id FROM usuarios WHERE usuario = 'admin'")[0]["id"]

    # Listagens não mostram nada da outra empresa
    for pagina in ("/", "/telas", "/usuarios", "/relatorios"):
        html = joao.get(pagina).get_data(as_text=True)
        for segredo in ("segredo-do-dono", "Tela do dono", "Grupo do dono", tela["codigo"]):
            assert segredo not in html, (pagina, segredo)

    # Ações pelo id da outra empresa dão 404 e não mudam nada
    for url in (
        f"/propaganda/{pid}/atualizar", f"/propaganda/{pid}/mover/cima", f"/propaganda/{pid}/excluir",
        f"/telas/{tela['id']}/atualizar", f"/telas/{tela['id']}/novo-codigo", f"/telas/{tela['id']}/excluir",
        f"/grupos/{gid}/excluir", f"/usuarios/{uid}/excluir", f"/usuarios/{uid}/desativar-2fa",
    ):
        assert postar(joao, url, {"nome": "invadido", "dias": list("0123456")}).status_code == 404, url
    assert consultar(dono, "SELECT nome FROM propagandas")[0]["nome"] == "segredo-do-dono.png"
    assert consultar(dono, "SELECT codigo FROM telas")[0]["codigo"] == tela["codigo"]
    assert len(consultar(dono, "SELECT * FROM grupos")) == 1

    # Não consegue apontar a própria propaganda para a tela da outra empresa
    enviar(joao, "do-joao.png", PNG)
    pid_joao = consultar(joao, "SELECT id FROM propagandas WHERE nome = 'do-joao.png'")[0]["id"]
    resposta = postar(joao, f"/propaganda/{pid_joao}/atualizar",
                      {"nome": "x", "duracao": "5", "ativo": "on", "dias": list("0123456"),
                       "destino": "escolher", "telas": [str(tela["id"])], "grupos": [str(gid)]})
    assert "pelo menos uma tela" in joao.get(resposta.headers["Location"]).get_data(as_text=True)
    assert consultar(dono, "SELECT * FROM propaganda_destinos") == []

    # Cliente não acessa a plataforma
    assert joao.get("/plataforma/").status_code == 403
    assert postar(joao, "/plataforma/empresas/nova", {"nome": "x"}).status_code == 403


def test_tv_mostra_so_propagandas_da_propria_empresa(duas_empresas):
    dono, joao, _ = duas_empresas
    enviar(dono, "do-dono.png", PNG)
    enviar(joao, "do-joao.png", PNG)
    postar(dono, "/letreiro", {"letreiro": "letreiro do dono"})
    tela_joao = criar_tela(joao, "TV do João")
    playlist = joao.get(f"/api/tela/{tela_joao['codigo']}/playlist").get_json()
    assert len(playlist["itens"]) == 1 and playlist["letreiro"] == ""
    pid_joao = consultar(joao, "SELECT id FROM propagandas WHERE nome = 'do-joao.png'")[0]["id"]
    assert playlist["itens"][0]["id"] == pid_joao


def test_relatorio_e_exportacao_isolados(duas_empresas):
    dono, joao, cliente_id = duas_empresas
    for cliente, nome in ((dono, "do-dono.png"), (joao, "do-joao.png")):
        enviar(cliente, nome, PNG)
        tela = criar_tela(cliente, "TV " + nome)
        pid = consultar(cliente, "SELECT id FROM propagandas WHERE nome = ?", nome)[0]["id"]
        agora = datetime.now(timezone.utc).isoformat()
        cliente.post(f"/api/tela/{tela['codigo']}/pulso",
                     json={"exibicoes": [{"propaganda_id": pid, "inicio": agora, "duracao": 7}]})
    assert "do-dono.png" not in joao.get("/relatorios").get_data(as_text=True)
    assert "do-dono.png" not in joao.get("/relatorios.csv").get_data(as_text=True)

    resposta = joao.get("/empresa/exportar")
    pacote = zipfile.ZipFile(io.BytesIO(resposta.data))
    dados = json.loads(pacote.read("dados.json"))
    assert dados["empresa"]["id"] == cliente_id
    assert [p["nome"] for p in dados["propagandas"]] == ["do-joao.png"]
    assert [u["usuario"] for u in dados["usuarios"]] == ["joao"]
    texto = json.dumps(dados)
    assert "senha_hash" not in texto and "pbkdf2" not in texto and "scrypt" not in texto
    assert "do-dono" not in pacote.read("exibicoes.csv").decode()
    assert len([n for n in pacote.namelist() if n.startswith("midia/")]) == 1


# ---------------------------------------------------------------------------
# Plano, suspensão e exclusão
# ---------------------------------------------------------------------------

def test_limites_do_plano(logado):
    criar_empresa(logado, "Plano Pequeno", "maria", limite_telas="1", limite_mb="0")
    maria = entrar(logado.application, "maria", "senha-do-cliente")
    criar_tela(maria, "Única")
    resposta = postar(maria, "/telas/nova", {"nome": "Segunda"}, pagina="/telas")
    assert "limite de telas" in maria.get(resposta.headers["Location"]).get_data(as_text=True)
    assert len(consultar(logado, "SELECT * FROM telas WHERE nome IN ('Única', 'Segunda')")) == 1

    antes = set(os.listdir(logado.application.config["PASTA_MIDIA"]))
    resposta = enviar(maria, "grande.png", PNG)
    assert "limite de armazenamento" in maria.get(resposta.headers["Location"]).get_data(as_text=True)
    assert set(os.listdir(logado.application.config["PASTA_MIDIA"])) == antes  # arquivo apagado


def test_suspender_empresa(duas_empresas):
    dono, joao, cliente_id = duas_empresas
    enviar(joao, "do-joao.png", PNG)
    tela = criar_tela(joao, "TV do João")
    postar(dono, f"/plataforma/empresas/{cliente_id}/atualizar", {"nome": "Padaria Cliente"}, pagina="/plataforma/")

    assert "/login" in joao.get("/").headers["Location"]            # sessão aberta derrubada
    novo = joao.application.test_client()
    bloqueado = novo.post("/login", data={"usuario": "joao", "senha": "senha-do-cliente", "csrf_token": csrf(novo)})
    assert bloqueado.status_code == 403
    assert joao.get(f"/api/tela/{tela['codigo']}/playlist").get_json()["itens"] == []  # a TV (aparelho do João)

    postar(dono, f"/plataforma/empresas/{cliente_id}/atualizar", {"nome": "Padaria Cliente", "ativa": "on"},
           pagina="/plataforma/")
    assert len(joao.get(f"/api/tela/{tela['codigo']}/playlist").get_json()["itens"]) == 1

    # A empresa principal nunca é suspensa
    postar(dono, "/plataforma/empresas/1/atualizar", {"nome": "Minha"}, pagina="/plataforma/")
    assert consultar(dono, "SELECT ativa FROM empresas WHERE id = 1")[0]["ativa"] == 1


def test_excluir_empresa_apaga_tudo(duas_empresas):
    dono, joao, cliente_id = duas_empresas
    enviar(joao, "do-joao.png", PNG)
    criar_tela(joao, "TV do João")
    arquivo = consultar(dono, "SELECT arquivo FROM propagandas WHERE empresa_id = ?", cliente_id)[0]["arquivo"]
    caminho = os.path.join(dono.application.config["PASTA_MIDIA"], arquivo)
    assert os.path.exists(caminho)

    postar(dono, f"/plataforma/empresas/{cliente_id}/excluir", {"confirmacao": "nome errado"}, pagina="/plataforma/")
    assert consultar(dono, "SELECT * FROM empresas WHERE id = ?", cliente_id)

    postar(dono, f"/plataforma/empresas/{cliente_id}/excluir", {"confirmacao": "Padaria Cliente"}, pagina="/plataforma/")
    for tabela in ("empresas", "usuarios", "telas", "propagandas"):
        coluna = "id" if tabela == "empresas" else "empresa_id"
        assert consultar(dono, f"SELECT * FROM {tabela} WHERE {coluna} = ?", cliente_id) == [], tabela
    assert not os.path.exists(caminho)

    postar(dono, "/plataforma/empresas/1/excluir", {"confirmacao": "Minha empresa"}, pagina="/plataforma/")
    assert consultar(dono, "SELECT * FROM empresas WHERE id = 1")


def test_criar_empresa_com_usuario_invalido_nao_deixa_empresa_orfa(logado):
    # Senha curta demais: o usuário não é criado e a empresa também não pode ficar.
    postar(logado, "/plataforma/empresas/nova", {"nome": "Repetida", "usuario": "admin", "senha": "curta"},
           pagina="/plataforma/")
    assert consultar(logado, "SELECT * FROM empresas WHERE nome = 'Repetida'") == []


def test_alertas_usam_destinatarios_da_empresa(app):
    app.config.update(ALERTA_WEBHOOK="https://plataforma.invalid", SMTP_HOST="smtp.invalid", ALERTA_EMAILS="dono@x.com")
    principal = {"id": 1, "alerta_emails": "", "alerta_webhook": ""}
    cliente = {"id": 2, "alerta_emails": "cliente@y.com", "alerta_webhook": ""}
    assert alertas.canais_da_empresa(principal, app.config).webhook == "https://plataforma.invalid"
    canais_cliente = alertas.canais_da_empresa(cliente, app.config)
    assert (canais_cliente.emails, canais_cliente.webhook, canais_cliente.nomes) == (("cliente@y.com",), "", ["e-mail"])


# ---------------------------------------------------------------------------
# Verificação em duas etapas
# ---------------------------------------------------------------------------

def segredo_da_pagina(cliente):
    html = cliente.get("/conta").get_data(as_text=True)
    return re.search(r'<p class="endereco">([A-Z2-7]+)</p>', html).group(1)


def test_ativar_e_usar_2fa(logado, monkeypatch):
    segredo = segredo_da_pagina(logado)
    # O segredo provisório fica no banco, não no cookie (que é assinado, mas legível).
    app = logado.application
    cookie = logado.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
    assert segredo not in str(app.session_interface.get_signing_serializer(app).loads(cookie))
    assert postar(logado, "/conta/2fa/ativar", {"codigo": "000000"}, pagina="/conta").status_code == 302
    assert consultar(logado, "SELECT totp_segredo FROM usuarios")[0]["totp_segredo"] is None  # código errado

    # Relógio falso só para o módulo de 2FA (o resto do sistema segue o relógio real).
    relogio = [1_900_000_000.0]

    class Relogio:
        @staticmethod
        def time():
            return relogio[0]

    monkeypatch.setattr(totp, "time", Relogio)
    postar(logado, "/conta/2fa/ativar", {"codigo": totp.codigo_atual(segredo)}, pagina="/conta")
    assert consultar(logado, "SELECT totp_segredo FROM usuarios")[0]["totp_segredo"] == segredo

    novo = app.test_client()
    etapa1 = novo.post("/login", data={"usuario": "admin", "senha": "senha-forte-123", "csrf_token": csrf(novo)})
    assert etapa1.headers["Location"].endswith("/login/codigo")
    assert "/login" in novo.get("/").headers["Location"]  # senha sozinha não entra

    token = csrf(novo, "/login/codigo")
    assert novo.post("/login/codigo", data={"codigo": "123456", "csrf_token": token}).status_code == 401
    # O código usado na ativação não pode ser reutilizado
    assert novo.post("/login/codigo", data={"codigo": totp.codigo_atual(segredo), "csrf_token": token}).status_code == 401

    relogio[0] += 30
    ok = novo.post("/login/codigo", data={"codigo": totp.codigo_atual(segredo), "csrf_token": token})
    assert ok.headers["Location"].endswith("/")
    assert novo.get("/").status_code == 200


def test_administrador_desativa_2fa_de_quem_perdeu_o_celular(logado):
    postar(logado, "/usuarios/novo", {"usuario": "caixa", "senha": "senha-do-caixa", "papel": "editor"}, pagina="/usuarios")
    with logado.application.app_context():
        conexao = db.obter()
        with conexao:
            conexao.execute("UPDATE usuarios SET totp_segredo = ? WHERE usuario = 'caixa'", (totp.novo_segredo(),))
    uid = consultar(logado, "SELECT id FROM usuarios WHERE usuario = 'caixa'")[0]["id"]
    postar(logado, f"/usuarios/{uid}/desativar-2fa", pagina="/usuarios")
    caixa = entrar(logado.application, "caixa", "senha-do-caixa")
    assert caixa.get("/").status_code == 200


def test_desativar_propria_2fa_exige_senha_e_codigo(logado):
    segredo = segredo_da_pagina(logado)
    postar(logado, "/conta/2fa/ativar", {"codigo": totp.codigo_atual(segredo)}, pagina="/conta")
    postar(logado, "/conta/2fa/desativar", {"senha": "errada", "codigo": totp.codigo_atual(segredo)}, pagina="/conta")
    assert consultar(logado, "SELECT totp_segredo FROM usuarios")[0]["totp_segredo"] == segredo


# ---------------------------------------------------------------------------
# Migração e páginas públicas
# ---------------------------------------------------------------------------

def test_migra_banco_da_versao_2_mantendo_os_dados(tmp_path):
    pasta = tmp_path / "dados"
    (pasta / "midia").mkdir(parents=True)
    (pasta / "midia" / "a.png").write_bytes(PNG)
    banco = sqlite3.connect(pasta / "banco.sqlite3")
    banco.executescript(MIGRACOES[0] + MIGRACOES[1] + "PRAGMA user_version = 2;")
    banco.executescript("""
        INSERT INTO usuarios (usuario, senha_hash, papel, token_sessao) VALUES ('dono', 'h', 'admin', 't'), ('ed', 'h', 'editor', 't');
        INSERT INTO grupos (nome) VALUES ('SP');
        INSERT INTO telas (nome, codigo, grupo_id) VALUES ('Balcão', 'abc', 1);
        INSERT INTO propagandas (nome, arquivo, tipo, duracao, posicao, para_todas) VALUES ('oferta', 'a.png', 'imagem', 5, 1, 0);
        INSERT INTO propaganda_destinos (propaganda_id, grupo_id) VALUES (1, 1);
        INSERT INTO configuracoes (chave, valor) VALUES ('letreiro', 'olá');
        INSERT INTO exibicoes (tela_id, propaganda_id, propaganda_nome, exibido_em, duracao) VALUES (1, 1, 'oferta', '2026-10-01 10:00:00', 5);
    """)
    banco.close()

    app = create_app({"PASTA_DADOS": str(pasta), "TESTING": True, "SECRET_KEY": "x"})
    with app.app_context():
        c = db.obter()
        assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACOES)
        assert {(u["usuario"], u["plataforma"], u["empresa_id"]) for u in c.execute("SELECT * FROM usuarios")} == {
            ("dono", 1, 1), ("ed", 0, 1)}
        assert c.execute("SELECT empresa_id, tamanho FROM propagandas").fetchone()[:] == (1, len(PNG))
        assert c.execute("SELECT COUNT(*) FROM propaganda_destinos").fetchone()[0] == 1
        assert db.ler_config(1, "letreiro") == "olá"
        assert c.execute("SELECT empresa_id FROM exibicoes").fetchone()[0] == 1
        assert c.execute("SELECT empresa_id, grupo_id FROM telas").fetchone()[:] == (1, 1)
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    # A TV antiga continua funcionando
    assert len(app.test_client().get("/api/tela/abc/playlist").get_json()["itens"]) == 1


def test_paginas_legais_publicas(cliente):
    assert "LGPD" in cliente.get("/privacidade").get_data(as_text=True)
    assert cliente.get("/termos").status_code == 200


def test_lote_e_pausa_nao_alcancam_outra_empresa(duas_empresas):
    dono, joao, _ = duas_empresas
    enviar(dono, "do-dono.png", PNG)
    pid = consultar(dono, "SELECT id FROM propagandas")[0]["id"]

    # O cliente marca a propaganda do dono pelo id: nada acontece.
    postar(joao, "/lote", {"ids": [str(pid)], "acao": "excluir"})
    postar(joao, "/lote", {"ids": [str(pid)], "acao": "desativar"})
    assert consultar(dono, "SELECT ativo FROM propagandas WHERE id = ?", pid)[0][0] == 1

    # A pausa do cliente não para as TVs do dono.
    postar(joao, "/pausa", {"acao": "pausar"})
    assert dono.get("/api/playlist").get_json()["pausado"] is False
    assert [i["id"] for i in dono.get("/api/playlist").get_json()["itens"]] == [pid]


def test_trocar_o_codigo_da_loja(duas_empresas):
    dono, joao, cliente_id = duas_empresas
    antigo = consultar(dono, "SELECT slug FROM empresas WHERE id = ?", cliente_id)[0][0]
    dados = {"nome": "Padaria Cliente", "alerta_emails": "", "alerta_webhook": ""}

    resposta = postar(joao, "/empresa", {**dados, "codigo": "padaria-do-joao"}, pagina="/empresa", follow_redirects=True)
    assert "/entrar/padaria-do-joao" in resposta.get_data(as_text=True)
    assert consultar(dono, "SELECT slug FROM empresas WHERE id = ?", cliente_id)[0][0] == "padaria-do-joao"
    assert joao.application.test_client().get(f"/entrar/{antigo}").status_code == 404

    # Inválido, reservado ou de outra loja: não muda.
    codigo_do_dono = consultar(dono, "SELECT slug FROM empresas WHERE id = 1")[0][0]
    for ruim in ("Com Espaço", "-x", "ab", "admin", codigo_do_dono):
        postar(joao, "/empresa", {**dados, "codigo": ruim}, pagina="/empresa")
        assert consultar(dono, "SELECT slug FROM empresas WHERE id = ?", cliente_id)[0][0] == "padaria-do-joao"


def test_administrador_muda_papel_e_senha_de_quem_e_da_equipe(duas_empresas):
    dono, joao, _ = duas_empresas
    postar(joao, "/usuarios/novo", {"usuario": "ana", "senha": "senha-da-ana-1", "papel": "editor"}, pagina="/usuarios")
    ana_id = consultar(dono, "SELECT id FROM usuarios WHERE usuario = 'ana'")[0]["id"]
    ana = entrar(joao.application, "ana", "senha-da-ana-1")
    assert ana.get("/usuarios").status_code == 403

    # Vira administradora: vale na hora, sem sair e entrar de novo.
    postar(joao, f"/usuarios/{ana_id}/editar", {"papel": "admin"}, pagina="/usuarios")
    assert ana.get("/usuarios").status_code == 200

    # Senha nova (esqueceu a dela): as sessões abertas caem e a senha antiga para de valer.
    postar(joao, f"/usuarios/{ana_id}/editar", {"papel": "admin", "senha": "senha-nova-da-ana"}, pagina="/usuarios")
    assert "/login" in ana.get("/").headers["Location"]
    assert entrar(joao.application, "ana", "senha-nova-da-ana").get("/usuarios").status_code == 200

    # Senha curta ou papel inexistente: nada muda.
    postar(joao, f"/usuarios/{ana_id}/editar", {"papel": "editor", "senha": "curta"}, pagina="/usuarios")
    postar(joao, f"/usuarios/{ana_id}/editar", {"papel": "dono"}, pagina="/usuarios")
    assert consultar(dono, "SELECT papel FROM usuarios WHERE id = ?", ana_id)[0][0] == "admin"

    # O próprio papel não muda (a loja nunca fica sem administrador), nem o de outra loja.
    joao_id = consultar(dono, "SELECT id FROM usuarios WHERE usuario = 'joao'")[0]["id"]
    assert postar(joao, f"/usuarios/{joao_id}/editar", {"papel": "editor"}, pagina="/usuarios").status_code == 403
    admin_id = consultar(dono, "SELECT id FROM usuarios WHERE usuario = 'admin'")[0]["id"]
    assert postar(joao, f"/usuarios/{admin_id}/editar", {"papel": "editor"}, pagina="/usuarios").status_code == 404


def test_plataforma_cria_empresa_com_cadastro(logado):
    postar(logado, "/plataforma/empresas/nova",
           {"nome": "Loja do Zé", "razao_social": "José Lanches Ltda", "documento": "36.740.823/0001-09",
            "email": "ze@exemplo.com", "telefone": "(99) 98436-9495", "usuario": "ze", "senha": "senha-do-ze-1"},
           pagina="/plataforma/")
    linha = consultar(logado, "SELECT * FROM empresas WHERE nome = 'Loja do Zé'")[0]
    assert (linha["razao_social"], linha["documento"], linha["telefone"]) == ("José Lanches Ltda", "36740823000109", "(99) 98436-9495")
    ze = entrar(logado.application, "ze", "senha-do-ze-1")
    assert '<span class="empresa-atual">José Lanches Ltda</span>' in ze.get("/").get_data(as_text=True)

    # CPF/CNPJ errado: a empresa não é criada.
    postar(logado, "/plataforma/empresas/nova",
           {"nome": "Errada", "documento": "123", "usuario": "errado", "senha": "senha-do-ze-1"}, pagina="/plataforma/")
    assert consultar(logado, "SELECT * FROM empresas WHERE nome = 'Errada'") == []
