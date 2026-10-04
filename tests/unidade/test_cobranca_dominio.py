"""Regras da cobrança sem banco e sem Asaas de verdade: faturas, atraso, webhook desconfiado e assinatura."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from src.domain.cobranca import (
    DadosDoPlano,
    EmpresaCobrada,
    ErroDeCobranca,
    ErroNoGateway,
    Fatura,
    Plano,
    ServicoDeCobranca,
    ler_preco,
)
from src.domain.documentos import documento_valido, so_numeros
from src.domain.erros import NaoEncontrado

HOJE = date(2026, 10, 4)
CPF = "52998224725"


def test_documentos():
    assert so_numeros("529.982.247-25") == CPF
    assert documento_valido("529.982.247-25") and documento_valido("11.222.333/0001-81")
    for errado in ("529.982.247-24", "111.111.111-11", "11.222.333/0001-80", "123", None):
        assert not documento_valido(errado)


def test_preco_digitado():
    assert [ler_preco(t) for t in ("49,90", "1.234,56", "49.9", "R$ 10", "0", "abc", "-5", "", "0,005")] == \
        [4990, 123456, 4990, 1000, 0, None, None, None, 1]


def test_fatura_do_asaas():
    fatura = Fatura.do_gateway({"id": "pay_1", "value": 49.9, "dueDate": "2026-10-10", "status": "RECEIVED",
                                "invoiceUrl": "https://asaas.com/i/1", "paymentDate": "2026-10-09"})
    assert fatura == Fatura("pay_1", 4990, "2026-10-10", "RECEIVED", "https://asaas.com/i/1", "2026-10-09")
    estranha = Fatura.do_gateway({"id": "pay_2", "value": "x", "invoiceUrl": "javascript:alert(1)", "deleted": True})
    assert (estranha.valor_centavos, estranha.link, estranha.status, estranha.em_aberto) == (0, None, "DELETED", False)
    assert Fatura.do_gateway({"id": "pay_3"}).status == "PENDING"


@pytest.mark.parametrize(("empresa", "atrasada", "decisao"), [
    (EmpresaCobrada(1, "A", cobranca_automatica=True), True, "suspender"),
    (EmpresaCobrada(1, "A", cobranca_automatica=False), True, None),          # cobrança manual: não suspende
    (EmpresaCobrada(1, "A", ativa=False, motivo_suspensao="inadimplencia"), False, "reativar"),
    (EmpresaCobrada(1, "A", ativa=False, motivo_suspensao="manual"), False, None),   # suspensão manual fica
    (EmpresaCobrada(1, "A", ativa=False, motivo_suspensao="inadimplencia"), True, None),
    (EmpresaCobrada(1, "A", cobranca_automatica=True), False, None),
])
def test_decisao_de_inadimplencia(empresa, atrasada, decisao):
    assert empresa.decidir_inadimplencia(atrasada) == decisao


def test_dados_do_plano():
    assert DadosDoPlano("  Básico ", 4990, 2, None, "painel", " x " * 200).conferido().nome == "Básico"
    with pytest.raises(ErroDeCobranca, match="módulo"):
        DadosDoPlano("Básico", 4990, None, None, "").conferido()
    with pytest.raises(ErroDeCobranca, match="nome e o preço"):
        DadosDoPlano("Básico", None, None, None, "painel").conferido()


# -- serviço ----------------------------------------------------------------------------------

class Repo:
    def __init__(self):
        self.empresas = {1: EmpresaCobrada(1, "Plataforma"), 2: EmpresaCobrada(2, "Lanchonete", documento=CPF,
                                                                                  email_cobranca="a@b.com")}
        self.planos = {7: Plano(7, "Básico", 4990, 2, 500, "painel"), 8: Plano(8, "Grátis", 0)}
        self.faturas: dict[str, tuple[int, Fatura]] = {}
        self.eventos: set[str] = set()

    def _muda(self, empresa_id, **campos):
        self.empresas[empresa_id] = replace(self.empresas[empresa_id], **campos)

    def empresa(self, empresa_id):
        return self.empresas.get(empresa_id)

    def empresa_da_assinatura(self, assinatura_id):
        return next((e for e in self.empresas.values() if e.asaas_assinatura_id == assinatura_id), None)

    def empresas_com_assinatura(self):
        return [e for e in self.empresas.values() if e.asaas_assinatura_id]

    def empresas_com_cobranca_automatica(self):
        return [e.id for e in self.empresas.values() if e.cobranca_automatica]

    def plano(self, plano_id):
        return self.planos.get(plano_id)

    def plano_com_nome(self, nome):
        return any(p.nome.lower() == nome.lower() for p in self.planos.values())

    def inserir_plano(self, dados):
        novo = max(self.planos) + 1
        self.planos[novo] = Plano(novo, dados.nome, dados.preco_centavos, dados.limite_telas, dados.limite_mb, dados.modulos)
        return novo

    def atualizar_plano(self, plano_id, dados):
        self.planos[plano_id] = replace(self.planos[plano_id], nome=dados.nome, preco_centavos=dados.preco_centavos)

    def gravar_dados_de_cobranca(self, empresa_id, plano, documento, email, automatica):
        self._muda(empresa_id, plano_id=plano.id if plano else None, documento=documento, email_cobranca=email,
                   cobranca_automatica=automatica)

    def gravar_contato(self, empresa_id, documento, email):
        self._muda(empresa_id, documento=documento, email_cobranca=email)

    def gravar_cliente_no_gateway(self, empresa_id, cliente_id):
        self._muda(empresa_id, asaas_cliente_id=cliente_id)

    def gravar_assinatura(self, empresa_id, assinatura_id, automatica):
        self._muda(empresa_id, asaas_assinatura_id=assinatura_id, cobranca_automatica=automatica)

    def gravar_plano_da_empresa(self, empresa_id, plano):
        self._muda(empresa_id, plano_id=plano.id, cobranca_automatica=True)

    def tirar_plano(self, empresa_id):
        self._muda(empresa_id, plano_id=None, asaas_assinatura_id=None, cobranca_automatica=False)

    def suspender_por_inadimplencia(self, empresa_id):
        self._muda(empresa_id, ativa=False, motivo_suspensao="inadimplencia")

    def reativar(self, empresa_id):
        self._muda(empresa_id, ativa=True, motivo_suspensao=None)

    def gravar_fatura(self, empresa_id, fatura, agora):
        self.faturas[fatura.asaas_id] = (empresa_id, fatura)

    def tem_fatura_vencida_antes_de(self, empresa_id, dia):
        return any(e == empresa_id and f.status == "OVERDUE" and f.vencimento < dia.isoformat()
                   for e, f in self.faturas.values())

    def faturas_em_aberto(self, empresa_id):
        return [i for i, (e, f) in self.faturas.items() if e == empresa_id and f.em_aberto]

    def cancelar_faturas(self, asaas_ids):
        for i in asaas_ids:
            e, f = self.faturas[i]
            self.faturas[i] = (e, replace(f, status="DELETED"))

    def evento_processado(self, evento_id):
        return evento_id in self.eventos

    def marcar_evento(self, evento_id, agora):
        self.eventos.add(evento_id)


class Asaas:
    def __init__(self):
        self.chamadas, self.pagamentos, self.erro, self.recusar = [], {}, None, set()

    def _registrar(self, *chamada):
        self.chamadas.append(chamada)
        if self.erro or chamada[0] in self.recusar:
            raise ErroNoGateway(self.erro or "recusado")

    def criar_cliente(self, nome, documento, email, referencia):
        self._registrar("cliente", nome, documento, referencia)
        return {"id": "cus_1"}

    def criar_assinatura(self, cliente_id, valor, vencimento, descricao, referencia):
        self._registrar("assinatura", cliente_id, valor, vencimento, descricao)
        return {"id": "sub_1"}

    def atualizar_assinatura(self, assinatura_id, valor, descricao):
        self._registrar("atualizar", assinatura_id, valor)
        return {}

    def cancelar_assinatura(self, assinatura_id):
        self._registrar("cancelar", assinatura_id)
        return {}

    def faturas_da_assinatura(self, assinatura_id):
        self._registrar("faturas", assinatura_id)
        return [p for p in self.pagamentos.values() if p.get("subscription") == assinatura_id]

    def buscar_fatura(self, fatura_id):
        self._registrar("buscar", fatura_id)
        if fatura_id not in self.pagamentos:
            raise ErroNoGateway("Cobrança não encontrada.")
        return self.pagamentos[fatura_id]


def pagamento(id_="pay_1", status="PENDING", vencimento=HOJE, assinatura="sub_1"):
    return {"id": id_, "subscription": assinatura, "value": 49.9, "status": status, "dueDate": vencimento.isoformat()}


def montar(hoje=HOJE):
    repo, asaas, avisos, dia = Repo(), Asaas(), [], [hoje]
    servico = ServicoDeCobranca(repo, asaas, empresa_principal=1, tolerancia_dias=5, hoje=lambda: dia[0],
                                relogio=lambda: datetime(2026, 10, 4, 12, tzinfo=timezone.utc), avisar_plataforma=avisos.append)
    return servico, repo, asaas, avisos, dia


def ativo(servico, repo):
    repo._muda(2, plano_id=7)
    servico.ativar(2, HOJE + timedelta(days=3))


def test_ativar_cria_cliente_e_assinatura_uma_vez():
    servico, repo, asaas, *_ = montar()
    repo._muda(2, plano_id=7)
    for vencimento, mensagem in ((None, "primeiro vencimento"), (HOJE - timedelta(days=1), "primeiro vencimento")):
        with pytest.raises(ErroDeCobranca, match=mensagem):
            servico.ativar(2, vencimento)
    asaas.recusar = {"assinatura"}
    with pytest.raises(ErroDeCobranca, match="O Asaas recusou: recusado"):
        servico.ativar(2, HOJE)
    assert repo.empresa(2).asaas_assinatura_id is None
    asaas.recusar = set()
    assert servico.ativar(2, HOJE).nome == "Básico"
    empresa = repo.empresa(2)
    assert (empresa.asaas_cliente_id, empresa.asaas_assinatura_id, empresa.cobranca_automatica) == ("cus_1", "sub_1", True)
    assert [c[0] for c in asaas.chamadas].count("cliente") == 1     # o cliente criado na tentativa recusada foi guardado
    assert ("assinatura", "cus_1", 4990, HOJE.isoformat(), "Plano Básico") in asaas.chamadas
    with pytest.raises(ErroDeCobranca, match="já está ativa"):
        servico.ativar(2, HOJE)


@pytest.mark.parametrize(("mudanca", "mensagem"), [
    ({"plano_id": None}, "plano com preço"),
    ({"plano_id": 8}, "plano com preço"),
    ({"plano_id": 7, "documento": "123"}, "CPF ou CNPJ válido"),
])
def test_ativar_confere_antes_de_chamar_o_asaas(mudanca, mensagem):
    servico, repo, asaas, *_ = montar()
    repo._muda(2, **mudanca)
    with pytest.raises(ErroDeCobranca, match=mensagem):
        servico.ativar(2, HOJE)
    assert asaas.chamadas == []


def test_atraso_alem_da_tolerancia_suspende_e_pagamento_reativa():
    servico, repo, asaas, avisos, dia = montar()
    ativo(servico, repo)
    asaas.pagamentos["pay_1"] = pagamento("pay_1", "OVERDUE", HOJE - timedelta(days=5))
    servico.sincronizar(repo.empresa(2))
    assert repo.empresa(2).ativa                                    # 5 dias: ainda na tolerância
    dia[0] = HOJE + timedelta(days=1)
    servico.sincronizar_todas()
    assert not repo.empresa(2).ativa and avisos[-1].startswith("⛔")
    asaas.pagamentos["pay_1"] = pagamento("pay_1", "RECEIVED", HOJE - timedelta(days=5))
    assert servico.processar_aviso({"id": "evt_1", "event": "PAYMENT_RECEIVED", "payment": pagamento("pay_1")}) == \
        {"ok": True, "empresa": 2, "resultado": "reativada"}
    assert repo.empresa(2).ativa and avisos[-1].startswith("💰")
    assert servico.processar_aviso({"id": "evt_1", "payment": pagamento("pay_1")}) == {"ok": True, "repetido": True}


def test_empresa_principal_e_suspensao_manual_nao_mudam():
    servico, repo, *_ = montar()
    repo._muda(1, cobranca_automatica=True)
    repo.gravar_fatura(1, Fatura("pay_x", 100, "2020-01-01", "OVERDUE"), None)
    assert servico.avaliar_inadimplencia(1) is None and repo.empresa(1).ativa
    repo._muda(2, ativa=False, motivo_suspensao="manual")
    assert servico.avaliar_inadimplencia(2) is None and not repo.empresa(2).ativa
    with pytest.raises(NaoEncontrado):
        servico.empresa(99)
    assert servico.avaliar_inadimplencia(99) is None


def test_webhook_nunca_acredita_no_conteudo():
    servico, repo, asaas, *_ = montar()
    ativo(servico, repo)
    repo._muda(2, ativa=False, motivo_suspensao="inadimplencia")
    falso = pagamento("pay_9", "RECEIVED")
    # o Asaas não conhece essa fatura: não libera e deixa o evento para a próxima tentativa
    assert servico.processar_aviso({"id": "evt_9", "payment": falso}) == {"ok": True, "pendente": True}
    assert "evt_9" not in repo.eventos and not repo.empresa(2).ativa
    # a fatura existe, mas é de outra assinatura
    asaas.pagamentos["pay_9"] = pagamento("pay_9", "RECEIVED", assinatura="sub_outra")
    assert servico.processar_aviso({"id": "evt_9", "payment": falso}) == {"ok": True, "ignorado": True}
    assert "pay_9" not in repo.faturas
    # assinatura desconhecida e evento sem pagamento: ignorados (e marcados)
    assert servico.processar_aviso({"id": "evt_x", "payment": pagamento(assinatura="sub_zzz")})["ignorado"]
    assert servico.processar_aviso({"id": "evt_y"})["ignorado"] and {"evt_x", "evt_y"} <= repo.eventos


def test_fatura_cancelada_no_asaas_sai_do_sistema():
    servico, repo, asaas, *_ = montar()
    ativo(servico, repo)
    asaas.pagamentos["pay_1"] = pagamento("pay_1", "OVERDUE", HOJE - timedelta(days=30))
    servico.sincronizar(repo.empresa(2))
    del asaas.pagamentos["pay_1"]                       # cancelada lá; o aviso se perdeu
    repo._muda(2, ativa=True, motivo_suspensao=None)
    servico.sincronizar(repo.empresa(2))
    assert repo.faturas["pay_1"][1].status == "DELETED" and repo.empresa(2).ativa


def test_asaas_fora_do_ar_numa_empresa_nao_para_as_outras():
    servico, repo, asaas, *_ = montar()
    ativo(servico, repo)
    repo.empresas[3] = EmpresaCobrada(3, "Outra", cobranca_automatica=True)
    repo.gravar_fatura(3, Fatura("pay_3", 100, "2026-01-01", "OVERDUE"), None)
    asaas.erro = "timeout"
    servico.sincronizar_todas()                          # sem exceção
    assert not repo.empresa(3).ativa                     # a tolerância foi aplicada mesmo assim


def test_dados_de_cobranca_da_plataforma():
    servico, repo, asaas, *_ = montar()
    for documento, email, mensagem in (("123", "", "CPF ou CNPJ inválido"), (CPF, "sem-arroba", "E-mail")):
        with pytest.raises(ErroDeCobranca, match=mensagem):
            servico.salvar_dados(2, 7, documento, email, True)
    servico.salvar_dados(2, 7, "529.982.247-25", " fin@loja.com ", True)
    assert (repo.empresa(2).documento, repo.empresa(2).email_cobranca, repo.empresa(2).plano_id) == (CPF, "fin@loja.com", 7)
    servico.ativar(2, HOJE)
    with pytest.raises(ErroDeCobranca, match="Cancele a cobrança antes"):
        servico.salvar_dados(2, None, CPF, "", True)
    repo.planos[9] = Plano(9, "Completo", 9990)
    servico.salvar_dados(2, 9, CPF, "", True)
    assert ("atualizar", "sub_1", 9990) in asaas.chamadas
    asaas.erro = "não pode"
    with pytest.raises(ErroDeCobranca, match="recusou a mudança de plano"):
        servico.salvar_dados(2, 7, CPF, "", True)
    assert repo.empresa(2).plano_id == 9


def test_cliente_assina_troca_e_desiste():
    servico, repo, asaas, avisos, _ = montar()
    with pytest.raises(ErroDeCobranca, match="empresa principal"):
        servico.assinar(1, 7, CPF, "a@b.com", 7, gateway_configurado=True)
    with pytest.raises(ErroDeCobranca, match="ainda não está disponível"):
        servico.assinar(2, 7, CPF, "a@b.com", 7, gateway_configurado=False)
    with pytest.raises(ErroDeCobranca, match="CPF ou CNPJ válido"):
        servico.assinar(2, 7, "000", "a@b.com", 7, gateway_configurado=True)
    plano, acao = servico.assinar(2, 7, None, None, 7, gateway_configurado=True)    # usa o CPF e e-mail já cadastrados
    assert (plano.id, acao) == (7, "assinou o")
    assert ("assinatura", "cus_1", 4990, (HOJE + timedelta(days=7)).isoformat(), "Plano Básico") in asaas.chamadas
    assert repo.empresa(2).cobranca_automatica and avisos[-1].startswith("💳 “Lanchonete” assinou o plano Básico (R$ 49,90/mês)")
    repo.planos[9] = Plano(9, "Completo", 9990)
    assert servico.assinar(2, 9, None, None, 7, gateway_configurado=True)[1] == "trocou para o"
    assert ("atualizar", "sub_1", 9990) in asaas.chamadas and repo.empresa(2).plano_id == 9
    asaas.erro = "fora do ar"
    with pytest.raises(ErroNoGateway):
        servico.desistir(2)
    assert repo.empresa(2).plano_id == 9                 # nada mudou
    asaas.erro = None
    servico.desistir(2)
    empresa = repo.empresa(2)
    assert (empresa.plano_id, empresa.asaas_assinatura_id, empresa.cobranca_automatica) == (None, None, False)
    assert avisos[-1] == "✖️ “Lanchonete” cancelou a assinatura."


def test_planos():
    servico, repo, *_ = montar()
    novo = servico.criar_plano(DadosDoPlano("Premium", 19990, 10, 2000, "painel,comanda"))
    with pytest.raises(ErroDeCobranca, match="já existe"):
        servico.criar_plano(DadosDoPlano("premium", 1, None, None, "painel"))
    with pytest.raises(ErroDeCobranca, match="pelo menos um módulo do plano"):
        servico.atualizar_plano(novo, DadosDoPlano("Premium", None, None, None, "painel"))
    with pytest.raises(NaoEncontrado):
        servico.atualizar_plano(999, DadosDoPlano("x", 1, None, None, "painel"))
    servico.atualizar_plano(novo, DadosDoPlano("Premium+", 24990, 10, 2000, "painel"))
    assert repo.planos[novo].preco_centavos == 24990


def test_cancelar_pela_plataforma_mantem_o_plano():
    servico, repo, asaas, *_ = montar()
    ativo(servico, repo)
    servico.cancelar(2)
    assert ("cancelar", "sub_1") in asaas.chamadas
    assert (repo.empresa(2).plano_id, repo.empresa(2).asaas_assinatura_id) == (7, None)
