from datetime import datetime

from propagandas.agenda import resumo_dias, resumo_horario
from src.domain.painel import Propaganda

BASE = {"ativo": True, "inicio": None, "fim": None, "dias_semana": "0123456", "hora_inicio": None, "hora_fim": None}
# 01/10/2026 é uma quinta-feira (weekday 3)
QUINTA_9H = datetime(2026, 10, 1, 9, 0)


def item(**campos):
    return Propaganda(id=1, nome="p", arquivo="p.png", tipo="imagem", duracao=10, **{**BASE, **campos})


def codigo(propaganda, agora=QUINTA_9H):
    return propaganda.situacao(agora)[0]


def test_periodo_de_validade():
    assert codigo(item(inicio="2026-09-01", fim="2026-10-31")) == "no_ar"
    assert codigo(item(inicio="2026-10-02")) == "agendada"
    assert codigo(item(fim="2026-09-30")) == "encerrada"
    assert codigo(item(ativo=False)) == "inativa"


def test_dias_da_semana():
    assert codigo(item(dias_semana="01234")) == "no_ar"      # seg a sex
    assert codigo(item(dias_semana="56")) == "fora_do_dia"   # só fim de semana


def test_faixa_de_horario():
    assert codigo(item(hora_inicio="06:00", hora_fim="10:00")) == "no_ar"
    assert codigo(item(hora_inicio="06:00", hora_fim="09:00")) == "fora_do_horario"  # fim é exclusivo
    assert codigo(item(hora_inicio="11:00")) == "fora_do_horario"
    assert codigo(item(hora_fim="12:00")) == "no_ar"


def test_faixa_que_vira_a_noite():
    noturna = item(hora_inicio="22:00", hora_fim="02:00")
    assert codigo(noturna, datetime(2026, 10, 1, 23, 30)) == "no_ar"
    assert codigo(noturna, datetime(2026, 10, 2, 1, 59)) == "no_ar"
    assert codigo(noturna, datetime(2026, 10, 2, 2, 0)) == "fora_do_horario"
    assert codigo(noturna, QUINTA_9H) == "fora_do_horario"


def test_resumos():
    assert resumo_dias("0123456") == "Todos os dias"
    assert resumo_dias("01234") == "Seg a Sex"
    assert resumo_dias("56") == "Sáb e Dom"
    assert resumo_dias("024") == "Seg, Qua, Sex"
    assert resumo_horario(None, None) == "o dia todo"
    assert resumo_horario("06:00", None) == "06:00 às 24:00"
