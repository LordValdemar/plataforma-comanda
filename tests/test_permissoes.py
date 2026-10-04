"""Permissões por papel (escolhidas pelo administrador) e autorização por QR code."""

import re
import time

from test_comanda_regras import abrir_comanda, criar_pessoa, criar_produto, postar_c

from conftest import csrf
from propagandas import db, permissoes


def pessoa(app, usuario, senha="senha-forte-123"):
    """Um celular novo, já logado."""
    cliente = app.test_client()
    cliente.post("/login", data={"usuario": usuario, "senha": senha, "csrf_token": csrf(cliente)})
    return cliente


def permitir(admin, **niveis):
    """permitir(admin, **{"telas.editor": 1}) — grava na tela de Permissões."""
    return postar_c(admin, "/permissoes", {chave: str(valor) for chave, valor in niveis.items()})


def consultar(app, sql, *parametros):
    with app.app_context():
        return db.obter().execute(sql, parametros).fetchall()


def codigo_do_qr(cliente, funcao):
    """Quem autoriza abre "Autorizar" e escolhe a função: aparece o QR (e o código para digitar)."""
    pagina = cliente.get(f"/autorizar?funcao={funcao}").get_data(as_text=True)
    return re.search(r'class="selo codigo-autorizacao">([A-Z0-9]{8})<', pagina).group(1)


def comanda_com_lanche(admin):
    lanche = criar_produto(admin, "X-Salada", "20,00")
    comanda_id = abrir_comanda(admin, 5)
    postar_c(admin, f"/comanda/{comanda_id}/itens", {f"qtd_{lanche}": "1"})
    return comanda_id


def test_padroes_iguais_ao_sistema_de_antes(logado, app):
    criar_pessoa(app, "ana", "editor")
    criar_pessoa(app, "maria", "garcom")
    ana, maria = pessoa(app, "ana"), pessoa(app, "maria")
    assert ana.get("/telas").status_code == 403
    assert ana.get("/relatorios").status_code == 200
    assert maria.get("/comanda/historico").status_code == 403
    pagina = logado.get("/permissoes").get_data(as_text=True)
    assert "Cadastrar e editar TVs" in pagina and "Fechar conta" in pagina


def test_administrador_libera_o_editor_a_cadastrar_tvs(logado, app):
    criar_pessoa(app, "ana", "editor")
    ana = pessoa(app, "ana")
    assert "Telas</a>" not in ana.get("/").get_data(as_text=True)

    permitir(logado, **{"telas.editor": permissoes.SIM})
    assert "Telas</a>" in ana.get("/").get_data(as_text=True)  # vale na hora, sem entrar de novo
    postar_c(ana, "/telas/nova", {"nome": "TV do balcão"})
    assert consultar(app, "SELECT nome FROM telas")[0][0] == "TV do balcão"
    # O que é só do administrador continua só dele.
    assert ana.get("/usuarios").status_code == 403
    assert "Alertas de tela offline" not in ana.get("/telas").get_data(as_text=True)

    # E pode tirar o "conectar TV" do editor.
    permitir(logado, **{"telas.editor": permissoes.NAO, "conectar_tv.editor": permissoes.NAO})
    assert ana.get("/telas").status_code == 403
    assert "Conectar uma TV" not in ana.get("/").get_data(as_text=True)
    assert postar_c(ana, "/telas/conectar", {"codigo": "ABCDEF"}).status_code == 403


def test_so_o_administrador_mexe_nas_permissoes(logado, app):
    criar_pessoa(app, "caixa", "caixa")
    caixa = pessoa(app, "caixa")
    assert caixa.get("/permissoes").status_code == 403
    assert permitir(caixa, **{"cardapio.caixa": permissoes.SIM}).status_code == 403
    assert caixa.get("/comanda/cardapio/").status_code == 403


def test_funcao_sem_autorizacao_nao_aceita_o_nivel(logado, app):
    criar_pessoa(app, "caixa", "caixa")
    permitir(logado, **{"vendas.caixa": permissoes.AUTORIZACAO, "cozinha.cozinha": permissoes.NAO})
    with app.app_context():
        assert permissoes.nivel_do_papel(1, "vendas", "caixa") == permissoes.SIM   # ficou o que era
        assert permissoes.nivel_do_papel(1, "cozinha", "cozinha") == permissoes.SIM  # fixa


def test_garcom_fecha_conta_com_o_qr_do_caixa(logado, app):
    comanda_id = comanda_com_lanche(logado)
    criar_pessoa(app, "maria", "garcom")
    criar_pessoa(app, "joana", "garcom")
    criar_pessoa(app, "caixa", "caixa")
    permitir(logado, **{"fechar_conta.garcom": permissoes.AUTORIZACAO})
    maria, joana, caixa = pessoa(app, "maria"), pessoa(app, "joana"), pessoa(app, "caixa")

    # O botão aparece, mas a página pede autorização.
    assert "Fechar conta" in maria.get(f"/comanda/{comanda_id}").get_data(as_text=True)
    resposta = maria.get(f"/comanda/{comanda_id}/fechar")
    assert resposta.status_code == 403 and "Precisa de autorização" in resposta.get_data(as_text=True)
    assert "Autorizar</a>" in caixa.get("/comanda/").get_data(as_text=True)
    assert "Autorizar</a>" not in maria.get("/comanda/").get_data(as_text=True)

    # O caixa mostra o QR; a garçonete lê e volta direto para a conta.
    codigo = codigo_do_qr(caixa, "fechar_conta")
    resposta = maria.get(f"/autorizacao/{codigo}")
    assert resposta.headers["Location"].endswith(f"/comanda/{comanda_id}/fechar")
    assert caixa.get(f"/api/autorizar/{codigo}").get_json() == {"situacao": "usado", "por": "maria"}
    postar_c(maria, f"/comanda/{comanda_id}/fechar", {"acao": "ajustar"})  # tira a taxa
    postar_c(maria, f"/comanda/{comanda_id}/fechar", {"acao": "pagar", "forma": "pix", "valor": "20"})
    postar_c(maria, f"/comanda/{comanda_id}/fechar", {"acao": "finalizar"})
    assert consultar(app, "SELECT status FROM cmd_comandas WHERE id = ?", comanda_id)[0][0] == "fechada"
    detalhe = consultar(app, "SELECT detalhe FROM cmd_auditoria WHERE acao = 'taxa de serviço'")[0][0]
    assert "autorizado por caixa" in detalhe
    registro = consultar(app, "SELECT * FROM autorizacoes")[0]
    assert registro["funcao"] == "fechar_conta" and registro["usado_em"]

    # O mesmo QR não serve para outra pessoa.
    joana.get(f"/autorizacao/{codigo}")
    assert joana.get(f"/comanda/{comanda_id}/fechar").status_code == 403

    # Passados os minutos da liberação, precisa de novo.
    with maria.session_transaction() as sessao:
        sessao["autorizacoes"]["fechar_conta"]["ate"] = time.time() - 1
    assert maria.get(f"/comanda/{comanda_id}/fechar").status_code == 403


def test_codigo_vencido_trocado_ou_do_proprio_autorizador_nao_vale(logado, app):
    comanda_id = comanda_com_lanche(logado)
    criar_pessoa(app, "maria", "garcom")
    criar_pessoa(app, "caixa", "caixa")
    permitir(logado, **{"fechar_conta.garcom": permissoes.AUTORIZACAO})
    maria, caixa = pessoa(app, "maria"), pessoa(app, "caixa")

    antigo = codigo_do_qr(caixa, "fechar_conta")
    novo = codigo_do_qr(caixa, "fechar_conta")              # abrir outro invalida o anterior
    maria.get(f"/autorizacao/{antigo}")
    assert maria.get(f"/comanda/{comanda_id}/fechar").status_code == 403

    with app.app_context():
        conexao = db.obter()
        with conexao:
            conexao.execute("UPDATE autorizacoes SET criado_em = '2020-01-01 00:00:00' WHERE codigo = ?", (novo,))
    assert "venceu" in maria.get(f"/autorizacao/{novo}", follow_redirects=True).get_data(as_text=True)

    # Quem autoriza não usa o próprio código (e o caixa nem precisa).
    terceiro = codigo_do_qr(caixa, "fechar_conta")
    assert "próprio código" in caixa.get(f"/autorizacao/{terceiro}", follow_redirects=True).get_data(as_text=True)
    # Código digitado (sem câmera) funciona igual.
    resposta = postar_c(maria, "/autorizacao/codigo", {"codigo": terceiro.lower()}, follow_redirects=True)
    assert "Autorizado por caixa" in resposta.get_data(as_text=True)
    assert maria.get(f"/comanda/{comanda_id}/fechar").status_code == 200


def test_quem_nao_pode_nem_com_autorizacao(logado, app):
    criar_pessoa(app, "maria", "garcom")
    criar_pessoa(app, "caixa", "caixa")
    permitir(logado, **{"fechar_conta.cozinha": permissoes.AUTORIZACAO})  # alguém da loja precisa
    maria, caixa = pessoa(app, "maria"), pessoa(app, "caixa")
    assert maria.get("/autorizar").status_code == 403           # garçom não autoriza nada
    codigo = codigo_do_qr(caixa, "fechar_conta")                # o garçom continua em "Não" (padrão)
    assert "nem com autorização" in maria.get(f"/autorizacao/{codigo}", follow_redirects=True).get_data(as_text=True)
    assert caixa.get("/autorizar?funcao=cardapio").status_code == 403  # o caixa não pode o cardápio


def test_desconto_com_autorizacao(logado, app):
    comanda_id = comanda_com_lanche(logado)
    criar_pessoa(app, "caixa", "caixa")
    criar_pessoa(app, "gerente", "caixa")
    permitir(logado, **{"desconto.caixa": permissoes.AUTORIZACAO})
    caixa = pessoa(app, "caixa")
    assert "com autorização" in caixa.get(f"/comanda/{comanda_id}/fechar").get_data(as_text=True)
    resposta = postar_c(caixa, f"/comanda/{comanda_id}/fechar", {"acao": "ajustar", "cobrar_taxa": "1", "desconto": "5"})
    assert resposta.status_code == 403
    assert consultar(app, "SELECT desconto_centavos FROM cmd_comandas")[0][0] == 0
    # Sem mudar o desconto, a taxa ela ajusta normalmente.
    assert postar_c(caixa, f"/comanda/{comanda_id}/fechar", {"acao": "ajustar", "desconto": "0,00"}).status_code == 302

    codigo = codigo_do_qr(logado, "desconto")  # o administrador autoriza
    caixa.get(f"/autorizacao/{codigo}")
    postar_c(caixa, f"/comanda/{comanda_id}/fechar", {"acao": "ajustar", "cobrar_taxa": "1", "desconto": "5"})
    assert consultar(app, "SELECT desconto_centavos FROM cmd_comandas")[0][0] == 500


def test_codigo_de_outra_loja_nao_vale(logado, app):
    from test_isolamento import criar_loja, entrar

    criar_pessoa(app, "maria", "garcom")
    permitir(logado, **{"fechar_conta.garcom": permissoes.AUTORIZACAO})
    criar_loja(logado, "Outra Loja", "outro")
    outro = entrar(app, "outro")
    codigo = codigo_do_qr(outro, "fechar_conta")
    maria = pessoa(app, "maria")
    assert "não vale" in maria.get(f"/autorizacao/{codigo}", follow_redirects=True).get_data(as_text=True)
    assert outro.get(f"/api/autorizar/{codigo}").get_json() == {"situacao": "aguardando"}
