"""Controle de ponto: entrada e saída, horário de trabalho, desconectar e relatório."""

from datetime import datetime
from zoneinfo import ZoneInfo

from conftest import csrf
from propagandas import auth, db, ponto


def token(cliente):
    with cliente.session_transaction() as sessao:
        sessao.setdefault("csrf", "token-de-teste")
        return sessao["csrf"]


def post(cliente, url, dados=None, **kwargs):
    return cliente.post(url, data={**(dados or {}), "csrf_token": token(cliente)}, **kwargs)


def consultar(app, sql, *parametros):
    with app.app_context():
        return db.obter().execute(sql, parametros).fetchall()


def criar_pessoa(app, usuario, papel="garcom", empresa_id=1):
    with app.app_context():
        auth.criar_usuario(db.obter(), empresa_id, usuario, "senha-forte-123", papel)
        return db.obter().execute("SELECT id FROM usuarios WHERE usuario = ? AND empresa_id = ?",
                                  (usuario, empresa_id)).fetchone()["id"]


def entrar(app, usuario):
    cliente = app.test_client()
    cliente.post("/login", data={"usuario": usuario, "senha": "senha-forte-123", "csrf_token": csrf(cliente)})
    return cliente


def ligar_ponto(admin):
    post(admin, "/ponto/ajustes", {"ativo": "1"})


def hoje():
    return str(datetime.now(ZoneInfo("America/Sao_Paulo")).weekday())


def test_sem_controle_ligado_a_equipe_entra_normalmente(logado, app):
    criar_pessoa(app, "joao")
    joao = entrar(app, "joao")
    assert joao.get("/comanda/").status_code == 200


def test_entrada_e_saida(logado, app):
    criar_pessoa(app, "joao")
    ligar_ponto(logado)
    joao = entrar(app, "joao")

    # Sem ponto aberto: tudo leva para a página do ponto, e a API da cozinha responde 401.
    assert joao.get("/comanda/").headers["Location"].endswith("/ponto")
    assert joao.get("/comanda/api/cozinha").status_code == 401
    assert "Registrar entrada" in joao.get("/ponto").get_data(as_text=True)

    post(joao, "/ponto/entrada")
    assert joao.get("/comanda/").status_code == 200
    assert "Ponto aberto" in joao.get("/comanda/").get_data(as_text=True)
    assert len(consultar(app, "SELECT * FROM ponto_registros WHERE saida IS NULL")) == 1

    # Entrar de novo não abre um segundo ponto.
    post(joao, "/ponto/entrada")
    assert len(consultar(app, "SELECT * FROM ponto_registros")) == 1

    resposta = post(joao, "/ponto/saida")
    assert resposta.headers["Location"].endswith("/login")
    registro = consultar(app, "SELECT * FROM ponto_registros")[0]
    assert registro["saida"] and registro["motivo_saida"] == "saída" and registro["usuario_nome"] == "joao"
    assert joao.get("/comanda/").headers["Location"].startswith("/login")


def test_administrador_nao_bate_ponto(logado, app):
    ligar_ponto(logado)
    assert logado.get("/comanda/").status_code == 200
    assert logado.get("/ponto").headers["Location"].endswith("/ponto/equipe")


def test_fora_do_horario_nao_entra_e_ponto_fecha_quando_o_horario_acaba(logado, app):
    joao_id = criar_pessoa(app, "joao")
    ligar_ponto(logado)
    joao = entrar(app, "joao")
    post(joao, "/ponto/entrada")

    # O administrador tira o dia de hoje do horário: o ponto aberto fecha no próximo acesso.
    outros_dias = [d for d in "0123456" if d != hoje()]
    post(logado, f"/ponto/pessoa/{joao_id}", {"dias": outros_dias})
    assert joao.get("/comanda/").headers["Location"].endswith("/ponto")
    registro = consultar(app, "SELECT * FROM ponto_registros")[0]
    assert registro["motivo_saida"] == "fim do horário"

    # E a entrada fica bloqueada.
    resposta = post(joao, "/ponto/entrada", follow_redirects=True)
    assert "Fora do seu horário" in resposta.get_data(as_text=True)
    assert len(consultar(app, "SELECT * FROM ponto_registros WHERE saida IS NULL")) == 0


def test_tarefa_fecha_ponto_de_quem_passou_do_horario(logado, app):
    joao_id = criar_pessoa(app, "joao")
    ligar_ponto(logado)
    joao = entrar(app, "joao")
    post(joao, "/ponto/entrada")
    with app.app_context():
        conexao = db.obter()
        with conexao:
            conexao.execute("UPDATE usuarios SET horario_dias = ? WHERE id = ?",
                            ("".join(d for d in "0123456" if d != hoje()), joao_id))
        ponto.fechar_fora_do_horario()
    assert consultar(app, "SELECT motivo_saida FROM ponto_registros")[0][0] == "fim do horário"


def test_desconectar_derruba_a_sessao_e_fecha_o_ponto(logado, app):
    joao_id = criar_pessoa(app, "joao")
    ligar_ponto(logado)
    joao = entrar(app, "joao")
    joao_celular = entrar(app, "joao")  # outro aparelho
    post(joao, "/ponto/entrada")

    post(logado, f"/ponto/pessoa/{joao_id}/desconectar")
    for aparelho in (joao, joao_celular):
        assert aparelho.get("/comanda/").headers["Location"].startswith("/login")
    registro = consultar(app, "SELECT * FROM ponto_registros")[0]
    assert registro["motivo_saida"] == "desconectado pelo administrador" and registro["encerrado_por"] == 1
    assert "desconectado pelo administrador: admin" in logado.get("/ponto/equipe").get_data(as_text=True)


def test_desconectar_funciona_mesmo_sem_ponto_e_nao_desconecta_a_si_mesmo(logado, app):
    joao_id = criar_pessoa(app, "joao")
    joao = entrar(app, "joao")
    post(logado, f"/ponto/pessoa/{joao_id}/desconectar", {"voltar": "usuarios"})
    assert joao.get("/comanda/").headers["Location"].startswith("/login")
    post(logado, "/ponto/pessoa/1/desconectar")
    assert logado.get("/comanda/").status_code == 200


def test_isento_e_validacoes_do_horario(logado, app):
    cozinha_id = criar_pessoa(app, "tablet", "cozinha")
    ligar_ponto(logado)
    post(logado, f"/ponto/pessoa/{cozinha_id}", {"dias": list("0123456"), "isento": "on"})
    tablet = entrar(app, "tablet")
    assert tablet.get("/comanda/cozinha").status_code == 200

    resposta = post(logado, f"/ponto/pessoa/{cozinha_id}", {"dias": list("0123456"), "inicio": "08:00"},
                    follow_redirects=True)
    assert "informe o horário de início e de fim" in resposta.get_data(as_text=True)
    resposta = post(logado, f"/ponto/pessoa/{cozinha_id}", {"inicio": "08:00", "fim": "17:00"}, follow_redirects=True)
    assert "pelo menos um dia" in resposta.get_data(as_text=True)


def test_so_administrador_mexe_no_ponto_da_equipe(logado, app):
    joao_id = criar_pessoa(app, "joao", "caixa")
    criar_pessoa(app, "maria", "caixa")
    maria = entrar(app, "maria")
    assert maria.get("/ponto/equipe").status_code == 403
    assert post(maria, f"/ponto/pessoa/{joao_id}/desconectar").status_code == 403
    assert post(maria, "/ponto/ajustes", {"ativo": "1"}).status_code == 403


def test_outra_loja_nao_alcanca_a_equipe(logado, app):
    joao_id = criar_pessoa(app, "joao")
    post(logado, "/plataforma/empresas/nova", {"nome": "Outra Loja", "usuario": "dono2", "senha": "senha-forte-123",
                                               "modulos_enviados": "1", "modulos": ["comanda"]})
    dono2 = entrar(app, "dono2")
    assert post(dono2, f"/ponto/pessoa/{joao_id}/desconectar").status_code == 404
    assert post(dono2, f"/ponto/pessoa/{joao_id}", {"dias": ["0"]}).status_code == 404
    assert "joao" not in dono2.get("/ponto/equipe").get_data(as_text=True)


def test_relatorio_csv(logado, app):
    criar_pessoa(app, "joao")
    ligar_ponto(logado)
    joao = entrar(app, "joao")
    post(joao, "/ponto/entrada")
    post(joao, "/ponto/saida")
    resposta = logado.get("/ponto/relatorio.csv")
    texto = resposta.get_data(as_text=True)
    assert resposta.mimetype == "text/csv" and "joao" in texto and "saída" in texto


def test_horario_que_vira_a_noite():
    sexta_das_18_as_2 = {"horario_dias": "4", "horario_inicio": "18:00", "horario_fim": "02:00"}
    fuso = ZoneInfo("America/Sao_Paulo")
    def em(dia, hora):  # 2026-10-02 é uma sexta-feira
        return datetime.fromisoformat(f"2026-10-{dia:02d}T{hora}").replace(tzinfo=fuso)
    assert ponto.no_horario(sexta_das_18_as_2, em(2, "19:00"))
    assert ponto.no_horario(sexta_das_18_as_2, em(3, "01:30"))      # sábado de madrugada: turno de sexta
    assert not ponto.no_horario(sexta_das_18_as_2, em(3, "19:00"))  # sábado à noite: não trabalha
    assert not ponto.no_horario(sexta_das_18_as_2, em(2, "10:00"))
    assert not ponto.no_horario(sexta_das_18_as_2, em(2, "01:30"))  # sexta de madrugada: turno de quinta
    dia_todo = {"horario_dias": "4", "horario_inicio": None, "horario_fim": None}
    assert ponto.no_horario(dia_todo, em(2, "03:00")) and not ponto.no_horario(dia_todo, em(3, "03:00"))
