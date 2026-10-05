"""Consultas da conta, da empresa, da cobrança e da plataforma no SQLite de verdade."""

import pytest

from propagandas import db
from src.infrastructure.sqlite import ConsultasDeEmpresas


@pytest.fixture
def conexao(app):
    conexao = db.conectar(app.config["BANCO"])
    with conexao:
        conexao.executemany("INSERT INTO planos (id, nome, preco_centavos, modulos, ativo) VALUES (?, ?, ?, ?, ?)",
                            [(5, "Básico", 4900, "painel", 1), (6, "Completo", 9900, "painel,comanda", 1),
                             (7, "Antigo", 2900, "painel", 0), (8, "Cortesia", 0, "painel", 1)])
        conexao.execute("INSERT INTO empresas (id, nome, slug, razao_social, plano_id, asaas_assinatura_id) "
                        "VALUES (2, 'Mercado', 'mercado', 'Mercado Ltda', 6, 'sub_1')")
        conexao.execute("INSERT INTO empresas (id, nome, slug, ativa) VALUES (3, 'Parada', 'parada', 0)")
        conexao.execute("INSERT INTO usuarios (id, empresa_id, usuario, senha_hash, papel, token_sessao, totp_segredo) "
                        "VALUES (9, 2, 'dono', 'hash-secreto', 'admin', 't', 'SEGREDO')")
        conexao.executemany("INSERT INTO faturas (empresa_id, asaas_id, valor_centavos, vencimento, status, atualizado_em) "
                            "VALUES (2, ?, 9900, ?, ?, 'x')",
                            [(f"pay_{n}", f"2026-{n:02d}-10", s) for n, s in
                             [(1, "RECEIVED"), (2, "OVERDUE"), (3, "OVERDUE"), (4, "PENDING"), (5, "DELETED"), (6, "RECEIVED"),
                              (7, "RECEIVED"), (8, "RECEIVED"), (9, "RECEIVED")]])
        conexao.execute("INSERT INTO exibicoes (empresa_id, tela_id, propaganda_id, propaganda_nome, exibido_em, duracao) "
                        "VALUES (2, NULL, 1, 'oferta', '2026-10-01 10:00:00', 5)")
    yield ConsultasDeEmpresas(conexao)
    conexao.close()


def test_empresa_e_pessoas(conexao):
    assert conexao.ficha(2)["razao_social"] == "Mercado Ltda" and conexao.ficha(99) is None
    assert conexao.ativa(2) and not conexao.ativa(3) and not conexao.ativa(99)
    assert conexao.loja_existe("MERCADO") and not conexao.loja_existe("outra")
    dono = conexao.usuario_com_loja(9)
    assert (dono["empresa_nome"], dono["empresa_slug"], dono["empresa_ativa"]) == ("Mercado Ltda", "mercado", 1)
    assert [u["usuario"] for u in conexao.usuarios_da_loja(2)] == ["dono"] and conexao.usuarios_da_loja(3) == []


def test_planos_e_faturas(conexao):
    assert [p["nome"] for p in conexao.planos_a_venda()] == ["Básico", "Completo"]   # sem o inativo e o gratuito
    assert conexao.plano_a_venda(7) is None and conexao.plano_a_venda(5)["nome"] == "Básico"
    assert conexao.plano(None) is None and conexao.plano(7)["nome"] == "Antigo"
    assert [f["vencimento"] for f in conexao.faturas(2, limite=3)] == ["2026-09-10", "2026-08-10", "2026-07-10"]
    assert all(f["status"] != "DELETED" for f in conexao.faturas(2))
    assert conexao.fatura_vencida(2)["vencimento"] == "2026-02-10"
    assert conexao.fatura_em_aberto(2)["vencimento"] == "2026-02-10" and conexao.fatura_em_aberto(3) is None
    assert conexao.tem_faturas(2) and not conexao.tem_faturas(3)


def test_plataforma(conexao):
    mercado = next(e for e in conexao.empresas_com_uso() if e["id"] == 2)
    assert (mercado["usuarios"], mercado["propagandas"], mercado["bytes"]) == (1, 0, 0)
    assert len(conexao.faturas_recentes(por_empresa=6)[2]) == 6
    assert conexao.receita_mensal() == 9900          # só a assinatura ativa (o plano da empresa 2)
    assert conexao.contato_das_telas() == []


def test_exportacao_sem_segredos(conexao):
    dados = conexao.dados_para_exportar(2)
    assert dados["empresa"]["nome"] == "Mercado"
    assert dados["usuarios"] == [{"usuario": "dono", "papel": "admin", "criado_em": dados["usuarios"][0]["criado_em"],
                                  "dois_fatores": 1}]
    assert "hash-secreto" not in str(dados) and "SEGREDO" not in str(dados)
    assert list(conexao.exibicoes_para_exportar(2)) == [("2026-10-01 10:00:00", None, 1, "oferta", 5)]
    assert list(conexao.exibicoes_para_exportar(3)) == []
