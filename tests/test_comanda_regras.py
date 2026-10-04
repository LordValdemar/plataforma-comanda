"""Regras da Comanda (portadas do sistema local), rodando como módulo da plataforma.

A empresa principal (logado) usa todos os módulos, então serve para testar a Comanda direto.
"""

import re

from conftest import csrf
from propagandas import db


def criar_pessoa(app, usuario, papel, senha="senha-forte-123"):
    from propagandas import auth
    with app.app_context():
        auth.criar_usuario(db.obter(), 1, usuario, senha, papel)


def entrar(cliente, usuario, senha="senha-forte-123"):
    cliente.post("/sair", data={"csrf_token": _token(cliente)})
    return cliente.post("/login", data={"usuario": usuario, "senha": senha, "csrf_token": csrf(cliente)})


def _token(cliente):
    with cliente.session_transaction() as sessao:
        sessao.setdefault("csrf", "token-de-teste")
        return sessao["csrf"]


def postar_c(cliente, url, dados=None, **kwargs):
    dados = dict(dados or {})
    dados["csrf_token"] = _token(cliente)
    return cliente.post(url, data=dados, **kwargs)


def criar_produto(cliente, nome, preco, cozinha=True, codigo="", categoria_id=""):
    dados = {"nome": nome, "preco": preco, "codigo": codigo, "categoria_id": categoria_id}
    if cozinha:
        dados["vai_cozinha"] = "on"
    postar_c(cliente, "/comanda/cardapio/produtos", dados)
    with cliente.application.app_context():
        return db.obter().execute("SELECT id FROM cmd_produtos WHERE nome = ? ORDER BY id DESC", (nome,)).fetchone()["id"]


def abrir_comanda(cliente, numero, mesa=""):
    resposta = postar_c(cliente, "/comanda/", {"numero": str(numero), "mesa": mesa})
    return int(resposta.headers["Location"].rstrip("/").split("/")[-1])



def preparar(logado):
    lanche = criar_produto(logado, "X-Salada", "20,00", codigo="1")
    lata = criar_produto(logado, "Refri lata", "6,00", cozinha=False)
    return lanche, lata


def itens(app):
    with app.app_context():
        return db.obter().execute("SELECT * FROM cmd_itens ORDER BY id").fetchall()


def test_abrir_e_numero_repetido(logado, app):
    comanda_id = abrir_comanda(logado, 7, mesa="3")
    assert "Comanda 7" in logado.get(f"/comanda/{comanda_id}").get_data(as_text=True)
    # Abrir de novo o mesmo número leva para a comanda já aberta.
    resposta = postar_c(logado, "/comanda/", {"numero": "7"})
    assert resposta.headers["Location"].endswith(f"/comanda/{comanda_id}")
    # Buscar pelo número também.
    assert logado.get("/comanda/?numero=7").headers["Location"].endswith(f"/comanda/{comanda_id}")
    assert "não está aberta" in logado.get("/comanda/?numero=8").get_data(as_text=True)


def test_lancar_pelo_cardapio_e_pelo_codigo(logado, app):
    lanche, lata = preparar(logado)
    comanda_id = abrir_comanda(logado, 1)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "2", f"obs_{lanche}": "sem cebola", f"qtd_{lata}": "1"})
    postar_c(logado, f"/comanda/{comanda_id}/itens", {"codigo": "1", "codigo_qtd": "1"})
    lista = itens(app)
    assert [(i["nome"], i["quantidade"], i["status"]) for i in lista] == [
        ("X-Salada", 2, "pendente"), ("Refri lata", 1, "entregue"), ("X-Salada", 1, "pendente"),
    ]
    assert lista[0]["observacao"] == "sem cebola"
    assert "R$ 66,00" in logado.get(f"/comanda/{comanda_id}").get_data(as_text=True)


def test_preco_fica_o_da_hora_do_pedido(logado, app):
    lanche, _ = preparar(logado)
    comanda_id = abrir_comanda(logado, 1)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1"})
    postar_c(logado, f"/comanda/cardapio/produtos/{lanche}", {"acao": "salvar", "nome": "X-Salada", "preco": "99", "vai_cozinha": "on"})
    assert itens(app)[0]["preco_centavos"] == 2000


def test_fluxo_da_cozinha(logado, app):
    lanche, lata = preparar(logado)
    comanda_id = abrir_comanda(logado, 5, mesa="2")
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1", f"qtd_{lata}": "1"})
    dados = logado.get("/comanda/api/cozinha").get_json()
    assert len(dados["comandas"]) == 1
    assert [i["nome"] for i in dados["comandas"][0]["itens"]] == ["X-Salada"]  # a lata não vai para a cozinha
    item_id = dados["comandas"][0]["itens"][0]["id"]

    token = {"X-CSRF-Token": _csrf_cozinha(logado)}
    url = f"/comanda/api/cozinha/itens/{item_id}"
    assert logado.post(url, data={"status": "preparando"}).status_code == 400  # sem CSRF
    assert logado.post(url, data={"status": "preparando"}, headers=token).get_json() == {"status": "preparando"}
    # Tocou errado: escolhe direto a situação certa, inclusive voltando.
    assert logado.post(url, data={"status": "pendente"}, headers=token).get_json() == {"status": "pendente"}
    assert logado.post(url, data={"status": "pronto"}, headers=token).get_json() == {"status": "pronto"}
    assert logado.post(url, data={"status": "cancelado"}, headers=token).status_code == 400
    # Pronto: aparece para o garçom servir.
    assert "Prontos para servir" in logado.get("/comanda/").get_data(as_text=True)
    postar_c(logado, f"/comanda/{comanda_id}/itens/{item_id}", {"acao": "entregue"})
    dados = logado.get("/comanda/api/cozinha").get_json()
    assert dados["comandas"] == []
    # O entregue fica embaixo, para a cozinha desfazer se foi engano.
    assert [i["id"] for i in dados["recentes"]] == [item_id]
    logado.post(url, data={"status": "pronto"}, headers=token)
    assert logado.get("/comanda/api/cozinha").get_json()["comandas"][0]["itens"][0]["status"] == "pronto"


def test_cozinha_tudo_pronto(logado, app):
    lanche, lata = preparar(logado)
    comanda_id = abrir_comanda(logado, 5)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "3", f"qtd_{lata}": "1"})
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1"})
    token = {"X-CSRF-Token": _csrf_cozinha(logado)}
    assert logado.post(f"/comanda/api/cozinha/comandas/{comanda_id}/pronto", headers=token).get_json() == {"alterados": 2}
    assert [i["status"] for i in itens(app)] == ["pronto", "entregue", "pronto"]  # a lata continua entregue


def test_garcom_desfaz_entregue(logado, app):
    lanche, lata = preparar(logado)
    comanda_id = abrir_comanda(logado, 6)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1", f"qtd_{lata}": "1"})
    lanche_item, lata_item = (i["id"] for i in itens(app))
    resposta = postar_c(logado, f"/comanda/{comanda_id}/itens/{lanche_item}", {"acao": "entregue", "voltar": "lista"},
                      follow_redirects=True)
    assert "Não entregue" in resposta.get_data(as_text=True)
    assert "Não entregue" in logado.get(f"/comanda/{comanda_id}").get_data(as_text=True)
    postar_c(logado, f"/comanda/{comanda_id}/itens/{lanche_item}", {"acao": "pronto"})
    assert itens(app)[0]["status"] == "pronto"
    # A lata não passa pela cozinha: não tem "pronto" para voltar.
    postar_c(logado, f"/comanda/{comanda_id}/itens/{lata_item}", {"acao": "pronto"})
    assert itens(app)[1]["status"] == "entregue"


def test_atualizacao_automatica_nao_gasta_avisos(logado):
    """A tela que se atualiza sozinha não pode sumir com o aviso que a pessoa ainda vai ver."""
    postar_c(logado, "/comanda/", {"numero": "abc"})  # gera o aviso de erro
    parcial = logado.get("/comanda/", headers={"X-Atualizacao": "1"}).get_data(as_text=True)
    assert 'id="regiao-abertas"' in parcial and 'id="regiao-prontos"' in parcial
    assert "Informe o número" not in parcial
    assert "Informe o número" in logado.get("/comanda/").get_data(as_text=True)


def _csrf_cozinha(cliente):
    html = cliente.get("/comanda/cozinha").get_data(as_text=True)
    return re.search(r'data-csrf="([^"]+)"', html).group(1)


def test_garcom_so_cancela_antes_da_cozinha(cliente, app):
    from conftest import configurar_admin
    configurar_admin(cliente)
    lanche, _ = preparar(cliente)
    criar_pessoa(app, "joao", "garcom")
    entrar(cliente, "joao")
    comanda_id = abrir_comanda(cliente, 1)
    postar_c(cliente, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1"})
    postar_c(cliente, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1"})
    primeiro, segundo = (i["id"] for i in itens(app))
    # Sem motivo, não cancela.
    postar_c(cliente, f"/comanda/{comanda_id}/itens/{primeiro}", {"acao": "cancelar", "motivo": ""})
    assert itens(app)[0]["status"] == "pendente"
    postar_c(cliente, f"/comanda/{comanda_id}/itens/{primeiro}", {"acao": "cancelar", "motivo": "lançado errado"})
    assert itens(app)[0]["status"] == "cancelado"
    with app.app_context():
        conexao = db.obter()
        with conexao:
            conexao.execute("UPDATE cmd_itens SET status = 'preparando' WHERE id = ?", (segundo,))
    resposta = postar_c(cliente, f"/comanda/{comanda_id}/itens/{segundo}", {"acao": "cancelar", "motivo": "x"}, follow_redirects=True)
    assert "Peça ao caixa" in resposta.get_data(as_text=True)
    # O garçom não fecha conta.
    assert cliente.get(f"/comanda/{comanda_id}/fechar").status_code == 403


def test_fechamento_com_taxa_desconto_e_troco(logado, app):
    lanche, lata = preparar(logado)
    comanda_id = abrir_comanda(logado, 9)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "2", f"qtd_{lata}": "1"})  # 46,00
    pagina = logado.get(f"/comanda/{comanda_id}/fechar").get_data(as_text=True)
    assert "R$ 4,60" in pagina and "R$ 50,60" in pagina  # taxa de 10%

    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "ajustar", "cobrar_taxa": "on", "desconto": "0,60"})  # 50,00
    # Cartão não pode passar do total.
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "credito", "valor": "60"})
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "pix", "valor": "30"})
    # Ainda faltam 20: não fecha.
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "finalizar"})
    with app.app_context():
        assert db.obter().execute("SELECT status FROM cmd_comandas").fetchone()[0] == "aberta"
    resposta = postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "dinheiro", "valor": "50"},
                      follow_redirects=True)
    assert "Troco: R$ 30,00" in resposta.get_data(as_text=True)
    resposta = postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "finalizar"})
    assert "/cupom" in resposta.headers["Location"]
    with app.app_context():
        comanda = db.obter().execute("SELECT * FROM cmd_comandas").fetchone()
        assert (comanda["status"], comanda["total_centavos"]) == ("fechada", 5000)
    cupom = logado.get(f"/comanda/{comanda_id}/cupom").get_data(as_text=True)
    assert "RECIBO" in cupom and "R$ 50,00" in cupom and "Troco" in cupom and "Não é documento fiscal" in cupom
    # Com a comanda fechada, o mesmo número pode ser aberto de novo.
    assert abrir_comanda(logado, 9) != comanda_id


def test_sem_taxa_e_conta_zerada(logado, app):
    lanche, _ = preparar(logado)
    comanda_id = abrir_comanda(logado, 2)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1"})
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "ajustar", "desconto": "20"})  # cortesia, sem taxa
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "finalizar"})
    with app.app_context():
        assert db.obter().execute("SELECT status, total_centavos FROM cmd_comandas").fetchone()[:] == ("fechada", 0)


def test_desconto_maior_que_a_conta(logado, app):
    lanche, _ = preparar(logado)
    comanda_id = abrir_comanda(logado, 2)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1"})
    resposta = postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "ajustar", "desconto": "500"}, follow_redirects=True)
    assert "não pode passar" in resposta.get_data(as_text=True)


def test_cancelar_comanda_e_reabrir(logado, app):
    lanche, _ = preparar(logado)
    comanda_id = abrir_comanda(logado, 3)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1"})
    postar_c(logado, f"/comanda/{comanda_id}/cancelar", {"motivo": "cliente desistiu"})
    with app.app_context():
        assert db.obter().execute("SELECT status FROM cmd_comandas").fetchone()[0] == "cancelada"
    assert itens(app)[0]["status"] == "cancelado"
    # Não dá para lançar em comanda cancelada.
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1"})
    assert len(itens(app)) == 1

    outra = abrir_comanda(logado, 4)
    postar_c(logado, f"/comanda/{outra}/itens", {f"qtd_{lanche}": "1"})
    postar_c(logado, f"/comanda/{outra}/fechar", {"acao": "pagar", "forma": "pix", "valor": "22"})
    postar_c(logado, f"/comanda/{outra}/fechar", {"acao": "finalizar"})
    postar_c(logado, f"/comanda/{outra}/reabrir")
    with app.app_context():
        conexao = db.obter()
        assert conexao.execute("SELECT status FROM cmd_comandas WHERE id = ?", (outra,)).fetchone()[0] == "aberta"
        acoes = [a["acao"] for a in conexao.execute("SELECT acao FROM cmd_auditoria ORDER BY id")]
    assert acoes == ["cancelar comanda", "reabrir comanda"]


def test_comanda_inexistente(logado):
    assert logado.get("/comanda/999").status_code == 404


def vender(logado, numero, produto, quantidade, forma, valor):
    comanda_id = abrir_comanda(logado, numero)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{produto}": str(quantidade)})
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": forma, "valor": valor})
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "finalizar"})
    return comanda_id


def test_relatorio_do_dia(logado):
    cerveja = criar_produto(logado, "Cerveja", "10", cozinha=False)
    vender(logado, 1, cerveja, 2, "pix", "22")        # 20 + 2 de taxa
    vender(logado, 2, cerveja, 1, "dinheiro", "20")   # 10 + 1 de taxa, troco 9
    abrir_comanda(logado, 3)
    html = logado.get("/comanda/relatorios/").get_data(as_text=True)
    assert "R$ 33,00" in html          # faturamento
    assert "R$ 16,50" in html          # ticket médio
    assert "R$ 3,00" in html           # taxa de serviço
    assert "PIX" in html and "Dinheiro" in html
    assert "1 comanda(s) ainda aberta(s)" in html
    assert "Fechadas e canceladas (2)" in logado.get("/comanda/historico").get_data(as_text=True)


def test_csv(logado):
    cerveja = criar_produto(logado, "Cerveja", "10", cozinha=False)
    vender(logado, 1, cerveja, 1, "debito", "11")
    resposta = logado.get("/comanda/relatorios/comandas.csv")
    texto = resposta.get_data(as_text=True)
    assert resposta.mimetype == "text/csv"
    assert "comanda;mesa" in texto and "11,00" in texto and "Cartão de débito 11,00" in texto


def test_periodo_invalido_usa_hoje(logado):
    assert logado.get("/comanda/relatorios/?de=lixo&ate=2020-13-40").status_code == 200






def test_ajustes_taxa(logado):
    postar_c(logado, "/comanda/ajustes/", {"nome_estabelecimento": "Lanchonete X", "taxa_servico": "12,5"})
    cerveja = criar_produto(logado, "Cerveja", "10", cozinha=False)
    comanda_id = abrir_comanda(logado, 1)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{cerveja}": "2"})
    assert "R$ 22,50" in logado.get(f"/comanda/{comanda_id}/fechar").get_data(as_text=True)
    resposta = postar_c(logado, "/comanda/ajustes/", {"taxa_servico": "50"}, follow_redirects=True)
    assert "vai de 0 a 30" in resposta.get_data(as_text=True)


def test_garcom_autorizado_fecha_conta(cliente, app):
    from conftest import configurar_admin
    configurar_admin(cliente)
    lanche, _ = preparar(cliente)
    criar_pessoa(app, "maria", "garcom")
    with app.app_context():
        maria = db.obter().execute("SELECT id FROM usuarios WHERE usuario = 'maria'").fetchone()["id"]
    comanda_id = abrir_comanda(cliente, 5)
    postar_c(cliente, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1"})  # 20,00 + 10%

    # Sem autorização, a garçonete não fecha.
    entrar(cliente, "maria")
    assert "Fechar conta" not in cliente.get(f"/comanda/{comanda_id}").get_data(as_text=True)
    assert cliente.get(f"/comanda/{comanda_id}/fechar").status_code == 403
    # Garçom não autoriza a si mesmo.
    assert postar_c(cliente, f"/usuarios/{maria}/fecha-conta").status_code in (302, 403)
    with app.app_context():
        assert db.obter().execute("SELECT fecha_conta FROM usuarios WHERE id = ?", (maria,)).fetchone()[0] == 0

    # O administrador autoriza.
    entrar(cliente, "admin")
    postar_c(cliente, f"/usuarios/{maria}/fecha-conta")
    assert "fecha contas" in cliente.get("/usuarios").get_data(as_text=True)

    entrar(cliente, "maria")
    assert "Fechar conta" in cliente.get(f"/comanda/{comanda_id}").get_data(as_text=True)
    pagina = cliente.get(f"/comanda/{comanda_id}/fechar").get_data(as_text=True)
    assert "Taxa de serviço" in pagina and 'name="desconto"' not in pagina
    # Desconto continua só com o caixa.
    assert postar_c(cliente, f"/comanda/{comanda_id}/fechar", {"acao": "ajustar", "desconto": "5"}).status_code == 403
    # A taxa de serviço ela pode tirar (fica no histórico da comanda).
    postar_c(cliente, f"/comanda/{comanda_id}/fechar", {"acao": "ajustar"})
    with app.app_context():
        conexao = db.obter()
        assert conexao.execute("SELECT cobrar_taxa FROM cmd_comandas WHERE id = ?", (comanda_id,)).fetchone()[0] == 0
        assert conexao.execute("SELECT usuario_id FROM cmd_auditoria WHERE acao = 'taxa de serviço'").fetchone()[0] == maria
    postar_c(cliente, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "pix", "valor": "20"})
    postar_c(cliente, f"/comanda/{comanda_id}/fechar", {"acao": "finalizar"})
    with app.app_context():
        comanda = db.obter().execute("SELECT status, fechada_por FROM cmd_comandas WHERE id = ?", (comanda_id,)).fetchone()
    assert comanda["status"] == "fechada" and comanda["fechada_por"] == maria
    # Cancelar a comanda e ver o histórico continuam com o caixa.
    assert cliente.get("/comanda/historico").status_code == 403

    # Tirando a autorização, volta a não fechar.
    entrar(cliente, "admin")
    postar_c(cliente, f"/usuarios/{maria}/fecha-conta")
    entrar(cliente, "maria")
    assert cliente.get(f"/comanda/{comanda_id}/fechar").status_code == 403


def test_so_garcom_recebe_a_permissao_de_fechar(logado, app):
    criar_pessoa(app, "chef", "cozinha")
    with app.app_context():
        chef = db.obter().execute("SELECT id FROM usuarios WHERE usuario = 'chef'").fetchone()["id"]
    assert postar_c(logado, f"/usuarios/{chef}/fecha-conta").status_code == 400


def test_taxa_arredonda_meio_centavo_para_cima_e_relatorio_bate_com_o_cupom(logado, app):
    from propagandas.comanda import formatos, relatorios
    from propagandas.comanda.formatos import hoje_local

    # Arredondamento comercial: o round() do Python daria 100 (meio para o par).
    assert formatos.porcentagem(1005, 10) == 101
    assert formatos.porcentagem(1004, 10) == 100
    assert formatos.porcentagem(999, 12.5) == 125      # 124,875 → 125
    assert formatos.porcentagem(1, 10) == 0

    produto = criar_produto(logado, "Pão de queijo", "10,05")
    comanda_id = abrir_comanda(logado, 7)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{produto}": "1"})
    pagina = logado.get(f"/comanda/{comanda_id}/fechar").get_data(as_text=True)
    assert "R$ 1,01" in pagina and "R$ 11,06" in pagina                 # taxa e total
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "pix", "valor": "11,06"})
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "finalizar"})
    with app.app_context():
        comanda = db.obter().execute("SELECT * FROM cmd_comandas WHERE id = ?", (comanda_id,)).fetchone()
        assert (comanda["status"], comanda["total_centavos"], comanda["taxa_centavos"]) == ("fechada", 1106, 101)
        resumo = relatorios.resumo(db.obter(), 1, hoje_local(), hoje_local())
    assert resumo["faturamento"] == 1106 and resumo["taxa"] == 101


def test_nao_paga_a_mais_nem_depois_de_fechada(logado, app):
    produto = criar_produto(logado, "Suco", "8,00", cozinha=False)
    comanda_id = abrir_comanda(logado, 8)
    postar_c(logado, f"/comanda/{comanda_id}/itens", {f"qtd_{produto}": "1"})  # 8,00 + 0,80
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "credito", "valor": "8,80"})
    # Já paga: outro pagamento (mesmo em dinheiro) é recusado.
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "dinheiro", "valor": "5"})
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "finalizar"})
    postar_c(logado, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "pix", "valor": "1"})
    with app.app_context():
        pagos = db.obter().execute("SELECT SUM(valor_centavos) FROM cmd_pagamentos WHERE comanda_id = ?", (comanda_id,)).fetchone()[0]
    assert pagos == 880
