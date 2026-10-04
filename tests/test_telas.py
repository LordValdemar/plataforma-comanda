import csv
import io
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from conftest import PNG, enviar, postar
from propagandas import agenda, alertas, create_app, db, tarefas


def consultar(cliente, sql, *parametros):
    with cliente.application.app_context():
        return db.obter().execute(sql, parametros).fetchall()


def criar_grupo(cliente, nome):
    postar(cliente, "/grupos/novo", {"nome": nome}, pagina="/telas")
    return consultar(cliente, "SELECT id FROM grupos WHERE nome = ?", nome)[0]["id"]


def criar_tela(cliente, nome, grupo_id=""):
    postar(cliente, "/telas/nova", {"nome": nome, "grupo_id": str(grupo_id)}, pagina="/telas")
    return consultar(cliente, "SELECT * FROM telas WHERE nome = ?", nome)[0]


def nova_propaganda(cliente, nome):
    enviar(cliente, nome, PNG)
    return consultar(cliente, "SELECT id FROM propagandas WHERE nome = ?", nome)[0]["id"]


def configurar(cliente, pid, **campos):
    dados = {"nome": "x", "duracao": "5", "ativo": "on", "dias": list("0123456"), "destino": "todas"}
    dados.update(campos)
    return postar(cliente, f"/propaganda/{pid}/atualizar", dados)


def ids_na_tela(cliente, tela):
    return [i["id"] for i in cliente.get(f"/api/tela/{tela['codigo']}/playlist").get_json()["itens"]]


def test_propaganda_direcionada_por_tela_e_grupo(logado):
    sp = criar_grupo(logado, "Lojas SP")
    balcao = criar_tela(logado, "Balcão", sp)
    vitrine = criar_tela(logado, "Vitrine")
    geral = nova_propaganda(logado, "geral.png")
    so_sp = nova_propaganda(logado, "sp.png")
    so_vitrine = nova_propaganda(logado, "vitrine.png")
    configurar(logado, so_sp, destino="escolher", grupos=[str(sp)])
    configurar(logado, so_vitrine, destino="escolher", telas=[str(vitrine["id"])])

    assert ids_na_tela(logado, balcao) == [geral, so_sp]
    assert ids_na_tela(logado, vitrine) == [geral, so_vitrine]
    # O player geral só mostra o que é para todas as telas.
    assert [i["id"] for i in logado.get("/api/playlist").get_json()["itens"]] == [geral]


def test_excluir_grupo_nao_espalha_propaganda_para_todas(logado):
    sp = criar_grupo(logado, "Lojas SP")
    vitrine = criar_tela(logado, "Vitrine")
    so_sp = nova_propaganda(logado, "sp.png")
    configurar(logado, so_sp, destino="escolher", grupos=[str(sp)])
    postar(logado, f"/grupos/{sp}/excluir", pagina="/telas")
    assert ids_na_tela(logado, vitrine) == []
    assert "Nenhuma tela: escolha um destino" in logado.get("/").get_data(as_text=True)


def test_validacoes_do_agendamento(logado):
    pid = nova_propaganda(logado, "a.png")
    for campos, mensagem in [
        ({"dias": []}, "pelo menos um dia"),
        ({"hora_inicio": "10:00", "hora_fim": "10:00"}, "não podem ser iguais"),
        ({"destino": "escolher"}, "pelo menos uma tela"),
    ]:
        resposta = configurar(logado, pid, **campos)
        assert mensagem in logado.get(resposta.headers["Location"]).get_data(as_text=True)


def test_horario_tira_do_ar(logado, monkeypatch):
    tela = criar_tela(logado, "Balcão")
    pid = nova_propaganda(logado, "cafe.png")
    configurar(logado, pid, hora_inicio="06:00", hora_fim="10:00", dias=["3"])  # quinta
    quinta = datetime(2026, 10, 1, 8, 0, tzinfo=agenda.ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agenda, "agora_local", lambda: quinta)
    assert ids_na_tela(logado, tela) == [pid]
    monkeypatch.setattr(agenda, "agora_local", lambda: quinta.replace(hour=11))
    assert ids_na_tela(logado, tela) == []


def test_letreiro_proprio_da_tela(logado):
    postar(logado, "/letreiro", {"letreiro": "geral"})
    tela = criar_tela(logado, "Balcão")
    url = f"/api/tela/{tela['codigo']}/playlist"
    assert logado.get(url).get_json()["letreiro"] == "geral"
    postar(logado, f"/telas/{tela['id']}/atualizar", {"nome": "Balcão", "letreiro": "só aqui"}, pagina="/telas")
    assert logado.get(url).get_json()["letreiro"] == "só aqui"


def test_codigo_da_tela(logado):
    tela = criar_tela(logado, "Balcão")
    anonimo = logado.application.test_client()
    assert anonimo.get(f"/tela/{tela['codigo']}").status_code == 200
    assert anonimo.get("/tela/codigo-inventado").status_code == 404
    assert anonimo.get("/api/tela/codigo-inventado/playlist").status_code == 404
    postar(logado, f"/telas/{tela['id']}/novo-codigo", pagina="/telas")
    assert anonimo.get(f"/tela/{tela['codigo']}").status_code == 404

    # O endereço usa o nome da tela (sem acentos) + 6 letras aleatórias.
    assert re.fullmatch(r"balcao-[a-z2-9]{6}", tela["codigo"])
    postar(logado, f"/telas/{tela['id']}/atualizar", {"nome": "Promoções da Semana"}, pagina="/telas")
    postar(logado, f"/telas/{tela['id']}/novo-codigo", pagina="/telas")
    novo = consultar(logado, "SELECT codigo FROM telas WHERE id = ?", tela["id"])[0][0]
    assert re.fullmatch(r"promocoes-da-semana-[a-z2-9]{6}", novo)
    assert anonimo.get(f"/tela/{novo}").status_code == 200


def test_pulso_registra_contato_e_exibicoes(logado):
    tela = criar_tela(logado, "Balcão")
    pid = nova_propaganda(logado, "oferta.png")
    agora = datetime.now(timezone.utc)
    registro = {"propaganda_id": pid, "inicio": agora.isoformat().replace("+00:00", "Z"), "duracao": 10.04}
    lote = [
        registro,
        registro,                                                  # repetido (reenvio): ignorado
        {"propaganda_id": pid, "inicio": "lixo", "duracao": 5},    # inválido
        {"propaganda_id": pid, "inicio": (agora + timedelta(days=2)).isoformat(), "duracao": 5},  # futuro
        {"propaganda_id": pid, "inicio": agora.isoformat(), "duracao": -1},
    ]
    # Rota das TVs não usa sessão: funciona sem CSRF e sem login.
    tv = logado.application.test_client()
    resposta = tv.post(f"/api/tela/{tela['codigo']}/pulso", json={"exibindo": pid, "exibicoes": lote})
    assert resposta.get_json() == {"recebidos": 5, "gravados": 2}
    tv.post(f"/api/tela/{tela['codigo']}/pulso", json={"exibicoes": [registro]})  # reenvio

    exibicoes = consultar(logado, "SELECT * FROM exibicoes")
    assert len(exibicoes) == 1 and exibicoes[0]["duracao"] == 10.0
    atualizada = consultar(logado, "SELECT * FROM telas WHERE id = ?", tela["id"])[0]
    assert atualizada["ultimo_contato"] and atualizada["ultimo_ip"] == "127.0.0.1"
    assert "Online" in logado.get("/telas").get_data(as_text=True)

    assert tv.post(f"/api/tela/{tela['codigo']}/pulso", data="nao é json").status_code == 400
    assert tv.post("/api/tela/inventado/pulso", json={}).status_code == 404


def test_outras_rotas_continuam_exigindo_csrf(logado):
    assert logado.post("/telas/nova", data={"nome": "x"}).status_code == 400


def test_telas_so_para_administrador(logado):
    postar(logado, "/usuarios/novo", {"usuario": "ed", "senha": "senha-do-ed", "papel": "editor"}, pagina="/usuarios")
    editor = logado.application.test_client()
    postar(editor, "/login", {"usuario": "ed", "senha": "senha-do-ed"}, pagina="/login")
    assert editor.get("/telas").status_code == 403
    assert editor.get("/relatorios").status_code == 200


def test_alerta_quando_tela_cai_e_volta(logado, monkeypatch):
    enviados = []
    monkeypatch.setattr(alertas, "enviar_alerta", lambda mensagem, canais, config=None: enviados.append(mensagem) or [])
    tela = criar_tela(logado, "Balcão")
    app = logado.application

    def contato_ha(minutos):
        momento = agenda.para_texto_utc(datetime.now(timezone.utc) - timedelta(minutes=minutos))
        with app.app_context():
            conexao = db.obter()
            with conexao:
                conexao.execute("UPDATE telas SET ultimo_contato = ? WHERE id = ?", (momento, tela["id"]))
            alertas.verificar_telas()

    contato_ha(1)
    assert enviados == []
    contato_ha(10)
    contato_ha(11)                        # continua fora: não repete o alerta
    assert len(enviados) == 1 and "sem comunicação" in enviados[0]
    contato_ha(0)
    assert len(enviados) == 2 and "voltou" in enviados[0 + 1]


def test_envio_real_por_webhook(app, monkeypatch):
    chamadas = []

    class Resposta:
        def __enter__(self):
            return self

        def __exit__(self, *erro):
            return False

        def read(self):
            return b"ok"

    monkeypatch.setattr(alertas._abridor, "open", lambda pedido, timeout: chamadas.append(pedido) or Resposta())
    monkeypatch.setattr(alertas.socket, "getaddrinfo", lambda host, porta: [(0, 0, 0, "", ("8.8.8.8", porta))])
    canais = {"emails": [], "webhook": "https://exemplo.invalid/webhook", "nomes": ["webhook"]}
    assert alertas.enviar_alerta("teste", canais, app.config) == []
    assert b'"text": "teste"' in chamadas[0].data


def test_relatorio_e_csv(logado):
    tela = criar_tela(logado, "Balcão")
    pid = nova_propaganda(logado, "oferta.png")
    agora = datetime.now(timezone.utc)
    lote = [{"propaganda_id": pid, "inicio": (agora - timedelta(minutes=m)).isoformat(), "duracao": 10} for m in range(3)]
    logado.post(f"/api/tela/{tela['codigo']}/pulso", json={"exibicoes": lote})

    pagina = logado.get("/relatorios").get_data(as_text=True)
    assert "oferta.png" in pagina and "30 s" in pagina

    resposta = logado.get("/relatorios.csv")
    assert resposta.mimetype == "text/csv"
    linhas = list(csv.reader(io.StringIO(resposta.get_data(as_text=True).lstrip("﻿")), delimiter=";"))
    assert linhas[0][0] == "Data"
    # Perto da meia-noite as 3 exibições podem cair em 2 dias: confere os totais.
    assert {(linha[1], linha[2]) for linha in linhas[1:]} == {("Balcão", "oferta.png")}
    assert sum(int(linha[3]) for linha in linhas[1:]) == 3
    assert sum(int(linha[4]) for linha in linhas[1:]) == 30

    # Excluir a propaganda não apaga o histórico.
    postar(logado, f"/propaganda/{pid}/excluir")
    assert "oferta.png" in logado.get("/relatorios").get_data(as_text=True)


def test_limpeza_de_exibicoes_antigas(logado):
    tela = criar_tela(logado, "Balcão")
    app = logado.application
    with app.app_context():
        conexao = db.obter()
        with conexao:
            for dias in (1, 400):
                momento = agenda.para_texto_utc(datetime.now(timezone.utc) - timedelta(days=dias))
                conexao.execute(
                    "INSERT INTO exibicoes (empresa_id, tela_id, propaganda_id, propaganda_nome, exibido_em, duracao) "
                    "VALUES (1, ?, 1, 'x', ?, 5)",
                    (tela["id"], momento),
                )
        tarefas.limpar_exibicoes_antigas(app.config)
    assert len(consultar(logado, "SELECT * FROM exibicoes")) == 1


def test_migra_banco_da_versao_anterior(tmp_path):
    from propagandas.db import MIGRACOES

    pasta = tmp_path / "dados"
    pasta.mkdir()
    banco = sqlite3.connect(pasta / "banco.sqlite3")
    banco.executescript(MIGRACOES[0] + "PRAGMA user_version = 1;")
    banco.execute("INSERT INTO propagandas (nome, arquivo, tipo, duracao, posicao) VALUES ('antiga', 'a.png', 'imagem', 5, 1)")
    banco.commit()
    banco.close()

    app = create_app({"PASTA_DADOS": str(pasta), "TESTING": True, "SECRET_KEY": "x"})
    with app.app_context():
        linha = db.obter().execute("SELECT * FROM propagandas").fetchone()
        assert linha["nome"] == "antiga" and linha["para_todas"] == 1 and linha["dias_semana"] == "0123456"
        assert db.obter().execute("PRAGMA user_version").fetchone()[0] == len(MIGRACOES)


def test_webhook_nao_acessa_rede_interna(logado, monkeypatch):
    """Proteção contra SSRF: um cliente não pode fazer o servidor chamar endereços internos."""
    import pytest

    def resolve_para(ip):
        monkeypatch.setattr(alertas.socket, "getaddrinfo", lambda host, porta: [(0, 0, 0, "", (ip, porta))])

    for ip in ("127.0.0.1", "10.0.0.5", "192.168.0.1", "169.254.169.254", "::1"):
        resolve_para(ip)
        with pytest.raises(alertas.EnderecoBloqueado):
            alertas.validar_url_webhook("https://parece-legitimo.com/hook")
    with pytest.raises(alertas.EnderecoBloqueado):
        alertas.validar_url_webhook("http://exemplo.com/hook")  # sem HTTPS

    resolve_para("169.254.169.254")
    resposta = postar(logado, "/empresa", {"nome": "Minha", "alerta_webhook": "https://metadados.exemplo/x"}, pagina="/empresa")
    assert "endereço interno" in resposta.get_data(as_text=True)
    assert consultar(logado, "SELECT alerta_webhook FROM empresas WHERE id = 1")[0]["alerta_webhook"] == ""


def test_mudar_telas_de_varias_propagandas_de_uma_vez(logado):
    sp, rj = criar_grupo(logado, "SP"), criar_grupo(logado, "RJ")
    tela_sp, tela_rj = criar_tela(logado, "Loja SP", sp), criar_tela(logado, "Loja RJ", rj)
    a, b, c = (nova_propaganda(logado, f"{n}.png") for n in "abc")

    # Trocar: a e b passam a aparecer só no grupo SP; c continua em todas.
    postar(logado, "/lote", {"ids": [str(a), str(b)], "acao": "telas", "modo": "trocar",
                             "destino": "escolher", "grupos": [str(sp)]})
    assert ids_na_tela(logado, tela_sp) == [a, b, c]
    assert ids_na_tela(logado, tela_rj) == [c]

    # Acrescentar: a ganha também o RJ, sem perder o SP.
    postar(logado, "/lote", {"ids": [str(a)], "acao": "telas", "modo": "acrescentar",
                             "destino": "escolher", "grupos": [str(rj)]})
    assert ids_na_tela(logado, tela_rj) == [a, c]
    assert a in ids_na_tela(logado, tela_sp)

    # Tirar: a e b saem do SP.
    postar(logado, "/lote", {"ids": [str(a), str(b)], "acao": "telas", "modo": "tirar",
                             "destino": "escolher", "grupos": [str(sp)]})
    assert ids_na_tela(logado, tela_sp) == [c]
    assert ids_na_tela(logado, tela_rj) == [a, c]

    # Todas as telas de volta, para as três.
    postar(logado, "/lote", {"ids": [str(a), str(b), str(c)], "acao": "telas", "destino": "todas"})
    assert ids_na_tela(logado, tela_sp) == [a, b, c] and ids_na_tela(logado, tela_rj) == [a, b, c]
    assert consultar(logado, "SELECT COUNT(*) FROM propaganda_destinos")[0][0] == 0


def test_ativar_desativar_tempo_e_excluir_em_lote(logado):
    tela = criar_tela(logado, "Balcão")
    a, b, c = (nova_propaganda(logado, f"{n}.png") for n in "abc")

    postar(logado, "/lote", {"ids": [str(a), str(c)], "acao": "desativar"})
    assert ids_na_tela(logado, tela) == [b]
    postar(logado, "/lote", {"ids": [str(a)], "acao": "ativar"})
    assert ids_na_tela(logado, tela) == [a, b]

    postar(logado, "/lote", {"ids": [str(a), str(b)], "acao": "tempo", "duracao": "25"})
    assert [r[0] for r in consultar(logado, "SELECT duracao FROM propagandas ORDER BY id")] == [25, 25, 7]

    # Sem nada marcado, não faz nada.
    resposta = postar(logado, "/lote", {"acao": "desativar"}, follow_redirects=True)
    assert "Marque pelo menos uma propaganda" in resposta.get_data(as_text=True)

    postar(logado, "/lote", {"ids": [str(b), str(c)], "acao": "excluir"})
    assert [r[0] for r in consultar(logado, "SELECT id FROM propagandas")] == [a]
    assert len(os.listdir(logado.application.config["PASTA_MIDIA"])) == 1


def test_pausar_todas_as_propagandas(logado):
    tela = criar_tela(logado, "Balcão")
    a = nova_propaganda(logado, "a.png")
    postar(logado, "/letreiro", {"letreiro": "Promoção"})

    postar(logado, "/pausa", {"acao": "pausar"})
    dados = logado.get(f"/api/tela/{tela['codigo']}/playlist").get_json()
    assert dados["pausado"] is True and dados["itens"] == [] and dados["letreiro"] == ""
    assert logado.get("/api/playlist").get_json()["pausado"] is True
    assert "Propagandas pausadas" in logado.get("/").get_data(as_text=True)

    postar(logado, "/pausa", {"acao": "retomar"})
    dados = logado.get(f"/api/tela/{tela['codigo']}/playlist").get_json()
    assert dados["pausado"] is False and [i["id"] for i in dados["itens"]] == [a]


def test_letreiro_proprio_por_propaganda(logado):
    tela = criar_tela(logado, "Balcão")
    a, b, c = (nova_propaganda(logado, f"{n}.png") for n in "abc")
    postar(logado, "/letreiro", {"letreiro": "Geral da loja"})

    configurar(logado, a, letreiro_modo="proprio", letreiro_texto="Só do bolo")
    configurar(logado, b, letreiro_modo="nenhum")
    dados = logado.get(f"/api/tela/{tela['codigo']}/playlist").get_json()
    assert dados["letreiro"] == "Geral da loja"
    assert [i["letreiro"] for i in dados["itens"]] == ["Só do bolo", "", None]

    # Em várias de uma vez: b e c ganham o mesmo texto; depois a volta ao geral.
    postar(logado, "/lote", {"ids": [str(b), str(c)], "acao": "letreiro", "letreiro_modo": "proprio", "letreiro_texto": "Promo"})
    postar(logado, "/lote", {"ids": [str(a)], "acao": "letreiro", "letreiro_modo": "geral"})
    itens = logado.get(f"/api/tela/{tela['codigo']}/playlist").get_json()["itens"]
    assert [i["letreiro"] for i in itens] == [None, "Promo", "Promo"]

    # "Texto próprio" sem texto não apaga nada.
    resposta = postar(logado, "/lote", {"ids": [str(b)], "acao": "letreiro", "letreiro_modo": "proprio", "letreiro_texto": " "},
                      follow_redirects=True)
    assert "Escreva o texto do letreiro" in resposta.get_data(as_text=True)
    assert "Letreiro próprio: “Promo”" in logado.get("/").get_data(as_text=True)
