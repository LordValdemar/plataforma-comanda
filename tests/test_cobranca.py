from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from conftest import PNG, csrf, enviar, postar
from propagandas import asaas, cobranca, db

TOKEN = "token-do-webhook-com-mais-de-32-caracteres"


class AsaasFalso:
    """Substitui a API do Asaas: registra as chamadas e devolve respostas prontas.

    `pagamentos` é a "verdade" do Asaas: o que a API responde quando o sistema consulta.
    """

    def __init__(self):
        self.chamadas = []
        self.pagamentos = []
        self.erro = None

    def registrar(self, pagamento_):
        self.pagamentos = [p for p in self.pagamentos if p["id"] != pagamento_["id"]] + [pagamento_]

    def __call__(self, metodo, url, chave, corpo=None):
        caminho = url.split("/v3", 1)[1]
        self.chamadas.append((metodo, caminho, corpo))
        if self.erro:
            raise asaas.ErroAsaas(self.erro)
        if metodo == "POST" and caminho == "/customers":
            return {"id": "cus_000001"}
        if metodo == "POST" and caminho == "/subscriptions":
            return {"id": "sub_000001", "status": "ACTIVE"}
        if metodo == "GET" and caminho.startswith("/subscriptions/sub_000001/payments"):
            return {"data": self.pagamentos, "hasMore": False}
        if metodo == "GET" and caminho.startswith("/payments/"):
            fatura_id = caminho.split("/")[2]
            for p in self.pagamentos:
                if p["id"] == fatura_id:
                    return p
            raise asaas.ErroAsaas("Cobrança não encontrada.")
        return {"id": caminho.rsplit("/", 1)[-1], "deleted": metodo == "DELETE"}


def hoje():
    """Data no fuso do sistema (America/Sao_Paulo): entre 21h e meia-noite, o UTC já está no dia seguinte."""
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def pagamento(id_="pay_1", status="PENDING", vencimento=None, valor=49.9):
    return {
        "object": "payment", "id": id_, "customer": "cus_000001", "subscription": "sub_000001",
        "value": valor, "status": status, "dueDate": (vencimento or hoje()).isoformat(),
        "invoiceUrl": f"https://sandbox.asaas.com/i/{id_}", "billingType": "UNDEFINED",
    }


@pytest.fixture
def falso(app, monkeypatch):
    app.config.update(ASAAS_API_KEY="$aact_hmlg_teste", ASAAS_WEBHOOK_TOKEN=TOKEN, COBRANCA_TOLERANCIA_DIAS=5)
    asaas_falso = AsaasFalso()
    monkeypatch.setattr(asaas, "_enviar", asaas_falso)
    monkeypatch.setattr(AsaasFalso, "atual", asaas_falso, raising=False)
    return asaas_falso


def consultar(cliente, sql, *parametros):
    with cliente.application.app_context():
        return db.obter().execute(sql, parametros).fetchall()


def empresa(cliente, empresa_id):
    return consultar(cliente, "SELECT * FROM empresas WHERE id = ?", empresa_id)[0]


@pytest.fixture
def cliente_com_plano(logado, falso):
    """Plataforma (logado) + empresa cliente com plano Básico e CNPJ válido, sem cobrança ativa ainda."""
    postar(logado, "/plataforma/empresas/nova", {"nome": "Mercado", "usuario": "mercado", "senha": "senha-do-mercado"},
           pagina="/plataforma/")
    empresa_id = consultar(logado, "SELECT id FROM empresas WHERE nome = 'Mercado'")[0]["id"]
    postar(logado, "/plataforma/planos/novo", {"nome": "Básico", "preco": "49,90", "limite_telas": "3", "limite_mb": "500"},
           pagina="/plataforma/")
    plano_id = consultar(logado, "SELECT id FROM planos")[0]["id"]
    postar(logado, f"/plataforma/empresas/{empresa_id}/cobranca",
           {"plano_id": str(plano_id), "documento": "11.222.333/0001-81", "email_cobranca": "fin@mercado.com"},
           pagina="/plataforma/")
    return logado, empresa_id


def ativar(plataforma, empresa_id, vencimento=None):
    return postar(plataforma, f"/plataforma/empresas/{empresa_id}/cobranca/ativar",
                  {"primeiro_vencimento": (vencimento or hoje()).isoformat()}, pagina="/plataforma/")


def webhook(cliente, evento, pagamento_, id_evento=None, token=TOKEN, verdadeiro=True):
    """Envia um aviso de webhook. Com verdadeiro=True, o Asaas confirma o mesmo conteúdo."""
    if verdadeiro and isinstance(pagamento_, dict) and "subscription" in pagamento_:
        AsaasFalso.atual.registrar(pagamento_)
    corpo = {"id": id_evento or f"evt_{evento}_{pagamento_['id']}", "event": evento, "payment": pagamento_}
    return cliente.application.test_client().post("/webhooks/asaas", json=corpo, headers={"asaas-access-token": token})


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def test_cpf_e_cnpj():
    assert cobranca.documento_valido("529.982.247-25")
    assert cobranca.documento_valido("11.222.333/0001-81")
    assert not cobranca.documento_valido("529.982.247-24")
    assert not cobranca.documento_valido("111.111.111-11")
    assert not cobranca.documento_valido("11.222.333/0001-80")
    assert not cobranca.documento_valido("123")


def test_valores_em_reais():
    assert cobranca.reais(4990) == "R$ 49,90"
    assert cobranca.reais(123456) == "R$ 1.234,56"
    assert cobranca.ler_reais("49,90") == 4990
    assert cobranca.ler_reais("1.234,56") == 123456
    assert cobranca.ler_reais("49.9") == 4990
    assert cobranca.ler_reais("abc") is None
    assert cobranca.ler_reais("-5") is None


# ---------------------------------------------------------------------------
# Planos e ativação
# ---------------------------------------------------------------------------

def test_plano_aplica_limites(cliente_com_plano):
    plataforma, empresa_id = cliente_com_plano
    e = empresa(plataforma, empresa_id)
    assert (e["limite_telas"], e["limite_mb"], e["documento"]) == (3, 500, "11222333000181")
    plano_id = e["plano_id"]
    postar(plataforma, f"/plataforma/planos/{plano_id}/atualizar",
           {"nome": "Básico", "preco": "59,90", "limite_telas": "4", "limite_mb": "500", "ativo": "on"}, pagina="/plataforma/")
    assert empresa(plataforma, empresa_id)["limite_telas"] == 4


def test_cnpj_invalido_e_recusado(cliente_com_plano):
    plataforma, empresa_id = cliente_com_plano
    resposta = postar(plataforma, f"/plataforma/empresas/{empresa_id}/cobranca", {"documento": "11.222.333/0001-80"},
                      pagina="/plataforma/")
    assert "CPF ou CNPJ inválido" in plataforma.get(resposta.headers["Location"]).get_data(as_text=True)
    assert empresa(plataforma, empresa_id)["documento"] == "11222333000181"


def test_ativar_cobranca_cria_cliente_e_assinatura(cliente_com_plano, falso):
    plataforma, empresa_id = cliente_com_plano
    vencimento = hoje() + timedelta(days=10)
    falso.pagamentos = [pagamento(vencimento=vencimento)]
    ativar(plataforma, empresa_id, vencimento)

    metodo, caminho, corpo = falso.chamadas[0]
    assert (metodo, caminho) == ("POST", "/customers")
    assert corpo["cpfCnpj"] == "11222333000181" and corpo["externalReference"] == f"empresa:{empresa_id}"
    metodo, caminho, corpo = falso.chamadas[1]
    assert (metodo, caminho) == ("POST", "/subscriptions")
    assert corpo == {
        "customer": "cus_000001", "billingType": "UNDEFINED", "value": 49.9, "nextDueDate": vencimento.isoformat(),
        "cycle": "MONTHLY", "description": "Plano Básico", "externalReference": f"empresa:{empresa_id}",
    }
    e = empresa(plataforma, empresa_id)
    assert (e["asaas_cliente_id"], e["asaas_assinatura_id"], e["cobranca_automatica"]) == ("cus_000001", "sub_000001", 1)
    faturas = consultar(plataforma, "SELECT * FROM faturas")
    assert len(faturas) == 1 and faturas[0]["valor_centavos"] == 4990 and faturas[0]["link"].endswith("/pay_1")
    assert "R$ 49,90" in plataforma.get("/plataforma/").get_data(as_text=True)


def test_ativar_exige_plano_e_documento(logado, falso):
    postar(logado, "/plataforma/empresas/nova", {"nome": "Sem dados", "usuario": "semdados", "senha": "senha-qualquer"},
           pagina="/plataforma/")
    empresa_id = consultar(logado, "SELECT id FROM empresas WHERE nome = 'Sem dados'")[0]["id"]
    resposta = ativar(logado, empresa_id)
    assert "escolha um plano" in logado.get(resposta.headers["Location"]).get_data(as_text=True)
    assert falso.chamadas == []


def test_erro_do_asaas_aparece_e_nada_e_gravado(cliente_com_plano, falso):
    plataforma, empresa_id = cliente_com_plano
    falso.erro = "O CPF/CNPJ informado é inválido."
    resposta = ativar(plataforma, empresa_id)
    assert "O CPF/CNPJ informado é inválido." in plataforma.get(resposta.headers["Location"]).get_data(as_text=True)
    assert empresa(plataforma, empresa_id)["asaas_assinatura_id"] is None


def test_mudar_plano_atualiza_assinatura(cliente_com_plano, falso):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    postar(plataforma, "/plataforma/planos/novo", {"nome": "Pro", "preco": "99,90"}, pagina="/plataforma/")
    pro = consultar(plataforma, "SELECT id FROM planos WHERE nome = 'Pro'")[0]["id"]
    postar(plataforma, f"/plataforma/empresas/{empresa_id}/cobranca",
           {"plano_id": str(pro), "documento": "11222333000181", "cobranca_automatica": "on"}, pagina="/plataforma/")
    assert ("PUT", "/subscriptions/sub_000001",
            {"value": 99.9, "description": "Plano Pro", "updatePendingPayments": True}) in falso.chamadas


def test_cancelar_cobranca(cliente_com_plano, falso):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    postar(plataforma, f"/plataforma/empresas/{empresa_id}/cobranca/cancelar", pagina="/plataforma/")
    assert ("DELETE", "/subscriptions/sub_000001", None) in falso.chamadas
    e = empresa(plataforma, empresa_id)
    assert e["asaas_assinatura_id"] is None and e["cobranca_automatica"] == 0


def test_somente_a_plataforma_mexe_na_cobranca(cliente_com_plano):
    plataforma, empresa_id = cliente_com_plano
    mercado = plataforma.application.test_client()
    mercado.post("/login", data={"usuario": "mercado", "senha": "senha-do-mercado", "csrf_token": csrf(mercado)})
    for url in ("/plataforma/planos/novo", f"/plataforma/empresas/{empresa_id}/cobranca/ativar",
                f"/plataforma/empresas/{empresa_id}/cobranca/cancelar"):
        assert postar(mercado, url, {"nome": "x", "preco": "0"}).status_code == 403


# ---------------------------------------------------------------------------
# Webhook, bloqueio e liberação automáticos
# ---------------------------------------------------------------------------

def test_webhook_exige_token(cliente_com_plano):
    plataforma, _ = cliente_com_plano
    assert webhook(plataforma, "PAYMENT_RECEIVED", pagamento(), token="errado").status_code == 401
    assert plataforma.application.test_client().post("/webhooks/asaas", json={}).status_code == 401


def test_atraso_suspende_e_pagamento_libera(cliente_com_plano):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    mercado = plataforma.application.test_client()
    mercado.post("/login", data={"usuario": "mercado", "senha": "senha-do-mercado", "csrf_token": csrf(mercado)})
    enviar(mercado, "oferta.png", PNG)
    postar(mercado, "/telas/nova", {"nome": "Caixa"}, pagina="/telas")
    codigo = consultar(plataforma, "SELECT codigo FROM telas WHERE empresa_id = ?", empresa_id)[0]["codigo"]

    # Vencida há 2 dias: dentro da tolerância (5), só avisa
    resposta = webhook(plataforma, "PAYMENT_OVERDUE", pagamento(status="OVERDUE", vencimento=hoje() - timedelta(days=2)))
    assert resposta.get_json()["resultado"] is None
    assert empresa(plataforma, empresa_id)["ativa"] == 1
    assert "fatura vencida" in mercado.get("/").get_data(as_text=True)

    # Vencida há 6 dias: suspende
    vencida = pagamento(status="OVERDUE", vencimento=hoje() - timedelta(days=6))
    assert webhook(plataforma, "PAYMENT_OVERDUE", vencida, id_evento="evt_2").get_json()["resultado"] == "suspensa"
    e = empresa(plataforma, empresa_id)
    assert (e["ativa"], e["motivo_suspensao"]) == (0, "inadimplencia")
    assert plataforma.get(f"/api/tela/{codigo}/playlist").get_json()["itens"] == []

    # O cliente entra, mas só vê a página de pagamento, com o link da fatura
    assert mercado.get("/").headers["Location"].endswith("/pagamento")
    assert mercado.get("/telas").headers["Location"].endswith("/pagamento")
    pagina = mercado.get("/pagamento").get_data(as_text=True)
    assert "suspenso por falta de pagamento" in pagina and "https://sandbox.asaas.com/i/pay_1" in pagina
    novo_login = plataforma.application.test_client()
    novo_login.post("/login", data={"usuario": "mercado", "senha": "senha-do-mercado", "csrf_token": csrf(novo_login)})
    assert novo_login.get("/").headers["Location"].endswith("/pagamento")

    # Pagou: libera sozinho
    pago = {**vencida, "status": "RECEIVED", "paymentDate": hoje().isoformat()}
    assert webhook(plataforma, "PAYMENT_RECEIVED", pago).get_json()["resultado"] == "reativada"
    assert empresa(plataforma, empresa_id)["ativa"] == 1
    assert mercado.get("/").status_code == 200
    assert len(plataforma.get(f"/api/tela/{codigo}/playlist").get_json()["itens"]) == 1


def test_evento_repetido_e_ignorado(cliente_com_plano):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    primeiro = webhook(plataforma, "PAYMENT_CREATED", pagamento("pay_9"), id_evento="evt_igual")
    assert primeiro.get_json()["empresa"] == empresa_id
    assert webhook(plataforma, "PAYMENT_CREATED", pagamento("pay_9"), id_evento="evt_igual").get_json()["repetido"]


def test_webhook_de_outra_assinatura_e_ignorado_com_200(cliente_com_plano):
    plataforma, _ = cliente_com_plano
    resposta = webhook(plataforma, "PAYMENT_RECEIVED", {**pagamento(), "subscription": "sub_de_outro_sistema"})
    assert resposta.status_code == 200 and resposta.get_json()["ignorado"]
    resposta = webhook(plataforma, "CUSTOMER_UPDATED", {"id": "x"})  # evento sem assinatura
    assert resposta.status_code == 200


def test_suspensao_manual_nao_e_desfeita_pelo_pagamento(cliente_com_plano):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    postar(plataforma, f"/plataforma/empresas/{empresa_id}/atualizar", {"nome": "Mercado"}, pagina="/plataforma/")
    assert empresa(plataforma, empresa_id)["motivo_suspensao"] == "manual"
    webhook(plataforma, "PAYMENT_RECEIVED", pagamento(status="RECEIVED"))
    assert empresa(plataforma, empresa_id)["ativa"] == 0


def test_tolerancia_vence_com_o_passar_dos_dias(cliente_com_plano):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    webhook(plataforma, "PAYMENT_OVERDUE", pagamento(status="OVERDUE", vencimento=hoje() - timedelta(days=1)))
    assert empresa(plataforma, empresa_id)["ativa"] == 1
    with plataforma.application.app_context():
        conexao = db.obter()
        assert cobranca.avaliar_inadimplencia(conexao, empresa_id, hoje=hoje() + timedelta(days=5)) == "suspensa"


def test_sem_bloqueio_automatico_nao_suspende(cliente_com_plano):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    postar(plataforma, f"/plataforma/empresas/{empresa_id}/cobranca",
           {"plano_id": str(empresa(plataforma, empresa_id)["plano_id"]), "documento": "11222333000181"},
           pagina="/plataforma/")  # sem "cobranca_automatica"
    webhook(plataforma, "PAYMENT_OVERDUE", pagamento(status="OVERDUE", vencimento=hoje() - timedelta(days=30)))
    assert empresa(plataforma, empresa_id)["ativa"] == 1


def test_sincronizacao_traz_faturas_perdidas(cliente_com_plano, falso):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    falso.pagamentos = [pagamento("pay_a", "RECEIVED"), pagamento("pay_b", "OVERDUE", hoje() - timedelta(days=9))]
    with plataforma.application.app_context():
        cobranca.sincronizar_todas()
    assert {f["asaas_id"] for f in consultar(plataforma, "SELECT asaas_id FROM faturas")} == {"pay_a", "pay_b"}
    assert empresa(plataforma, empresa_id)["motivo_suspensao"] == "inadimplencia"


def test_empresa_principal_nunca_e_suspensa(logado, falso):
    with logado.application.app_context():
        conexao = db.obter()
        cobranca.gravar_fatura(conexao, 1, pagamento(status="OVERDUE", vencimento=hoje() - timedelta(days=60)))
        with conexao:
            conexao.execute("UPDATE empresas SET cobranca_automatica = 1 WHERE id = 1")
        assert cobranca.avaliar_inadimplencia(conexao, 1) is None
    assert empresa(logado, 1)["ativa"] == 1


def test_ambiente_invalido_impede_inicio(tmp_path):
    from propagandas import create_app

    with pytest.raises(ValueError):
        create_app({"PASTA_DADOS": str(tmp_path), "TESTING": True, "SECRET_KEY": "x", "ASAAS_AMBIENTE": "produção"})


def test_excluir_empresa_cancela_assinatura(cliente_com_plano, falso):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    falso.erro = "fora do ar"
    postar(plataforma, f"/plataforma/empresas/{empresa_id}/excluir", {"confirmacao": "Mercado"}, pagina="/plataforma/")
    assert consultar(plataforma, "SELECT * FROM empresas WHERE id = ?", empresa_id)  # não excluiu sem cancelar
    falso.erro = None
    postar(plataforma, f"/plataforma/empresas/{empresa_id}/excluir", {"confirmacao": "Mercado"}, pagina="/plataforma/")
    assert ("DELETE", "/subscriptions/sub_000001", None) in falso.chamadas
    assert consultar(plataforma, "SELECT * FROM empresas WHERE id = ?", empresa_id) == []


def test_nao_tira_plano_com_cobranca_ativa(cliente_com_plano):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    postar(plataforma, f"/plataforma/empresas/{empresa_id}/cobranca", {"plano_id": "", "documento": "11222333000181"},
           pagina="/plataforma/")
    assert empresa(plataforma, empresa_id)["plano_id"] is not None


def test_link_da_fatura_so_aceita_https(cliente_com_plano):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    webhook(plataforma, "PAYMENT_CREATED", {**pagamento("pay_x"), "invoiceUrl": "javascript:alert(1)"})
    assert consultar(plataforma, "SELECT link FROM faturas WHERE asaas_id = 'pay_x'")[0]["link"] is None


# ---------------------------------------------------------------------------
# O webhook não é fonte da verdade
# ---------------------------------------------------------------------------

def test_aviso_falso_de_pagamento_nao_libera_cliente(cliente_com_plano, falso):
    """Mesmo com o token certo, um "pago" falso é desmentido pela consulta à API do Asaas."""
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    vencida = pagamento(status="OVERDUE", vencimento=hoje() - timedelta(days=10))
    webhook(plataforma, "PAYMENT_OVERDUE", vencida)
    assert empresa(plataforma, empresa_id)["ativa"] == 0

    falso_pago = {**vencida, "status": "RECEIVED", "value": 0.01}
    resposta = webhook(plataforma, "PAYMENT_RECEIVED", falso_pago, verdadeiro=False)
    assert resposta.get_json()["resultado"] is None
    assert empresa(plataforma, empresa_id)["ativa"] == 0
    fatura = consultar(plataforma, "SELECT * FROM faturas WHERE asaas_id = 'pay_1'")[0]
    assert (fatura["status"], fatura["valor_centavos"]) == ("OVERDUE", 4990)  # valeu o que o Asaas disse
    assert ("GET", "/payments/pay_1", None) in falso.chamadas


def test_fatura_de_outra_assinatura_e_ignorada(cliente_com_plano, falso):
    """Aviso apontando para a assinatura do cliente, mas a fatura real é de outro."""
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    falso.registrar({**pagamento("pay_outro", "RECEIVED"), "subscription": "sub_de_outra_pessoa"})
    resposta = webhook(plataforma, "PAYMENT_RECEIVED", pagamento("pay_outro", "RECEIVED"), verdadeiro=False)
    assert resposta.get_json()["ignorado"]
    assert consultar(plataforma, "SELECT * FROM faturas WHERE asaas_id = 'pay_outro'") == []


def test_asaas_fora_do_ar_deixa_para_a_sincronizacao(cliente_com_plano, falso):
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    falso.erro = "timeout"
    resposta = webhook(plataforma, "PAYMENT_RECEIVED", pagamento("pay_2", "RECEIVED"), id_evento="evt_x")
    assert resposta.status_code == 200 and resposta.get_json()["pendente"]
    assert consultar(plataforma, "SELECT * FROM webhook_eventos WHERE id = 'evt_x'") == []  # será reaplicado
    falso.erro = None
    assert webhook(plataforma, "PAYMENT_RECEIVED", pagamento("pay_2", "RECEIVED"), id_evento="evt_x").get_json()["empresa"]


def test_fatura_cancelada_no_asaas_sai_do_sistema(cliente_com_plano, falso):
    """Se o aviso de cancelamento se perder, a sincronização corrige (e não suspende à toa)."""
    plataforma, empresa_id = cliente_com_plano
    ativar(plataforma, empresa_id)
    webhook(plataforma, "PAYMENT_OVERDUE", pagamento("pay_3", "OVERDUE", hoje() - timedelta(days=2)))
    falso.pagamentos = []  # cancelada no Asaas; o aviso nunca chegou
    with plataforma.application.app_context():
        cobranca.sincronizar_todas()
        assert cobranca.avaliar_inadimplencia(db.obter(), empresa_id, hoje=hoje() + timedelta(days=30)) is None
    assert consultar(plataforma, "SELECT status FROM faturas WHERE asaas_id = 'pay_3'")[0]["status"] == "DELETED"
    assert empresa(plataforma, empresa_id)["ativa"] == 1


def test_sincronizacao_le_todas_as_paginas(monkeypatch):
    respostas = iter([
        {"data": [{"id": f"pay_{i}"} for i in range(100)], "hasMore": True},
        {"data": [{"id": "pay_100"}], "hasMore": False},
    ])
    monkeypatch.setattr(asaas, "chamar", lambda *args, **kwargs: next(respostas))
    assert len(asaas.faturas_da_assinatura("sub_x")) == 101
