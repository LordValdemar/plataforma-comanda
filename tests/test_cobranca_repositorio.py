"""Cobrança no SQLite de verdade e o cliente do Asaas (sem rede: o HTTP é substituído)."""

import io
import json
import urllib.error
from datetime import date, datetime, timezone

import pytest

from propagandas import db
from src.domain.cobranca import DadosDoPlano, ErroNoGateway, Fatura, Plano
from src.infrastructure import asaas as cliente_asaas
from src.infrastructure.asaas import ClienteAsaas, enviar_http
from src.infrastructure.sqlite import RepositorioDeCobrancaSQLite

AGORA = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


@pytest.fixture
def repo(app):
    conexao = db.conectar(app.config["BANCO"])
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (2, 'Lanchonete', 'lanchonete')")
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (3, 'Outra', 'outra')")
    yield RepositorioDeCobrancaSQLite(conexao)
    conexao.close()


def test_fatura_regravada_nao_perde_o_link_e_vencimento_conta_pelo_dia(repo):
    repo.gravar_fatura(2, Fatura("pay_1", 4990, "2026-09-28", "PENDING", "https://asaas.com/i/1"), AGORA)
    repo.gravar_fatura(2, Fatura("pay_1", 4990, "2026-09-28", "OVERDUE", None), AGORA)    # o aviso veio sem link
    linha = repo._c.execute("SELECT status, link FROM faturas WHERE asaas_id = 'pay_1'").fetchone()
    assert tuple(linha) == ("OVERDUE", "https://asaas.com/i/1")
    assert not repo.tem_fatura_vencida_antes_de(2, date(2026, 9, 28))     # vencida no dia: ainda não
    assert repo.tem_fatura_vencida_antes_de(2, date(2026, 9, 29))
    assert not repo.tem_fatura_vencida_antes_de(3, date(2030, 1, 1))      # de outra empresa
    repo.gravar_fatura(2, Fatura("pay_2", 4990, "2026-10-28", "RECEIVED"), AGORA)
    assert repo.faturas_em_aberto(2) == ["pay_1"] and repo.faturas_em_aberto(3) == []
    repo.cancelar_faturas(["pay_1"])
    assert not repo.tem_fatura_vencida_antes_de(2, date(2030, 1, 1))


def test_plano_novo_e_limites_que_valem_para_as_empresas_do_plano(repo):
    plano_id = repo.inserir_plano(DadosDoPlano("Básico", 4990, 2, 500, "painel"))
    assert repo.plano_com_nome("básico") and not repo.plano_com_nome("Premium")   # maiúsculas não importam
    plano = repo.plano(plano_id)
    repo.gravar_dados_de_cobranca(2, plano, "52998224725", "a@b.com", True)
    repo.atualizar_plano(plano_id, DadosDoPlano("Básico", 5990, 5, 900, "painel,comanda", "novo", ativo=False))
    linha = repo._c.execute("SELECT limite_telas, limite_mb, plano_id, cobranca_automatica FROM empresas WHERE id = 2").fetchone()
    assert tuple(linha) == (5, 900, plano_id, 1)
    assert repo.plano(plano_id) == Plano(plano_id, "Básico", 5990, 5, 900, "painel,comanda", "novo", False)
    assert repo._c.execute("SELECT limite_telas FROM empresas WHERE id = 3").fetchone()[0] is None


def test_assinatura_suspensao_e_desistencia(repo):
    plano_id = repo.inserir_plano(DadosDoPlano("Básico", 4990, 2, 500, "painel"))
    repo.gravar_cliente_no_gateway(2, "cus_1")
    repo.gravar_assinatura(2, "sub_1", automatica=True)
    repo.gravar_plano_da_empresa(2, repo.plano(plano_id))
    assert repo.empresa_da_assinatura("sub_1").id == 2
    assert [e.id for e in repo.empresas_com_assinatura()] == [2] and repo.empresas_com_cobranca_automatica() == [2]
    repo.suspender_por_inadimplencia(2)
    empresa = repo.empresa(2)
    assert (empresa.ativa, empresa.motivo_suspensao) == (False, "inadimplencia")
    repo.reativar(2)
    repo.tirar_plano(2)
    empresa = repo.empresa(2)
    assert (empresa.ativa, empresa.plano_id, empresa.asaas_assinatura_id, empresa.cobranca_automatica,
            empresa.asaas_cliente_id) == (True, None, None, False, "cus_1")   # o cliente no Asaas fica para a próxima vez


def test_eventos_do_webhook(repo):
    assert not repo.evento_processado("evt_1")
    repo.marcar_evento("evt_1", AGORA)
    repo.marcar_evento("evt_1", AGORA)                   # reenvio: não duplica
    assert repo.evento_processado("evt_1")


# -- cliente do Asaas ----------------------------------------------------------------------

def test_pedidos_que_o_cliente_monta():
    chamadas = []

    def chamar(metodo, caminho, corpo=None, parametros=None):
        chamadas.append((metodo, caminho, corpo, parametros))
        if caminho.endswith("/payments"):
            pagina = parametros["offset"] // 100
            return {"data": [{"id": f"pay_{pagina}_{i}"} for i in range(100 if pagina == 0 else 1)], "hasMore": pagina == 0}
        return {"id": "x"}

    asaas = ClienteAsaas(chamar)
    asaas.criar_assinatura("cus_1", 4990, "2026-10-10", "Plano Básico", "empresa:2")
    assert chamadas[-1][2] == {"customer": "cus_1", "billingType": "UNDEFINED", "value": 49.9, "nextDueDate": "2026-10-10",
                               "cycle": "MONTHLY", "description": "Plano Básico", "externalReference": "empresa:2"}
    asaas.atualizar_assinatura("sub_1", 9990, "Plano Completo")
    assert chamadas[-1][:2] == ("PUT", "/subscriptions/sub_1") and chamadas[-1][2]["updatePendingPayments"] is True
    assert len(asaas.faturas_da_assinatura("sub_1")) == 101
    sem_fim = ClienteAsaas(lambda *a, **k: {"data": [{"id": "p"}], "hasMore": True})
    with pytest.raises(ErroNoGateway, match="faturas demais"):
        sem_fim.faturas_da_assinatura("sub_1")


def test_erros_do_asaas_viram_mensagem(monkeypatch):
    def recusa(pedido, timeout):
        assert pedido.get_header("Access_token") == "chave" and pedido.get_header("User-agent")
        corpo = json.dumps({"errors": [{"description": "CPF inválido"}, {"description": "e-mail inválido"}]}).encode()
        raise urllib.error.HTTPError(pedido.full_url, 400, "Bad Request", {}, io.BytesIO(corpo))

    monkeypatch.setattr(cliente_asaas.urllib.request, "urlopen", recusa)
    with pytest.raises(ErroNoGateway, match="CPF inválido; e-mail inválido"):
        enviar_http("POST", "https://api-sandbox.asaas.com/v3/customers", "chave", {"name": "x"})

    def fora_do_ar(pedido, timeout):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(cliente_asaas.urllib.request, "urlopen", fora_do_ar)
    with pytest.raises(ErroNoGateway, match="não foi possível falar com o Asaas"):
        enviar_http("GET", "https://api-sandbox.asaas.com/v3/payments/x", "chave")
