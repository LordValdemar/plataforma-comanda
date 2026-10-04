"""Regras dos relatórios sem banco: período digitado, taxa de comandas antigas, ticket médio e planilhas."""

from datetime import date

from src.domain.periodo import Periodo
from src.domain.relatorios import ResumoDeExibicoes, ResumoDeVendas, taxa_da_comanda
from src.domain.relatorios.exibicoes import ExibicoesDaPropaganda, ExibicoesDoDia
from src.domain.relatorios.exibicoes import linhas_da_planilha as planilha_de_exibicoes
from src.domain.relatorios.vendas import ComandaEncerrada, linhas_da_planilha

HOJE = date(2026, 10, 4)


def test_periodo():
    assert Periodo.ler("2026-10-01", "2026-10-03", HOJE, HOJE) == Periodo(date(2026, 10, 1), date(2026, 10, 3))
    assert Periodo.ler("2026-10-03", "2026-10-01", HOJE, HOJE) == Periodo(date(2026, 10, 1), date(2026, 10, 3))   # invertido
    assert Periodo.ler("lixo", "2026-13-40", date(2026, 9, 28), HOJE) == Periodo(date(2026, 9, 28), HOJE)
    longo = Periodo.ler("2020-01-01", "2026-10-04", HOJE, HOJE, max_dias=92)
    assert (longo.fim, longo.dias) == (HOJE, 92)
    assert Periodo.ler(None, None, HOJE, HOJE).dias == 1


def test_taxa_e_ticket_medio():
    assert taxa_da_comanda(150, True, 10, 9999) == 150          # a gravada no fechamento vale
    assert taxa_da_comanda(None, True, 10, 1005) == 101         # antiga: recalculada, meio centavo para cima
    assert taxa_da_comanda(None, False, 10, 1005) == 0
    assert ResumoDeVendas(comandas=3, faturamento=1000).ticket_medio == 333
    assert ResumoDeVendas().ticket_medio == 0


def test_planilhas():
    fechada = ComandaEncerrada(7, "03", None, "joao", "fechada", "2026-10-04 12:00:00", "2026-10-04 13:00:00", 150, 4900,
                               [("pix", 2000), ("dinheiro", 2900)], None)
    cancelada = ComandaEncerrada(8, None, "Ana", None, "cancelada", "2026-10-04 12:00:00", "2026-10-04 12:10:00", 0, 0, [],
                                 "desistiu")
    linhas = linhas_da_planilha([fechada, cancelada], lambda texto: f"<{texto}>" if texto else "")
    assert linhas[0] == [7, "03", "", "joao", "fechada", "<2026-10-04 12:00:00>", "<2026-10-04 13:00:00>", "1,50", "49,00",
                         "PIX 20,00 Dinheiro 29,00"]
    assert linhas[1][-1] == "desistiu"
    dias = [ExibicoesDoDia(date(2026, 10, 3), "Balcão", "Café", 10, 99.6)]
    assert planilha_de_exibicoes(dias)[1] == ["03/10/2026", "Balcão", "Café", 10, 100]
    resumo = ResumoDeExibicoes([ExibicoesDaPropaganda(1, "A", False, 3, 30.0, 1), ExibicoesDaPropaganda(2, "B", True, 2, 10.5, 2)])
    assert (resumo.total_exibicoes, resumo.total_tempo) == (5, 40.5)
