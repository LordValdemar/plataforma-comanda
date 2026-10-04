"""Fuso horário, agendamento das propagandas e formatação de datas."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app

from src.domain.horario import DIAS, TODOS_OS_DIAS, resumo_dias, resumo_horario  # noqa: F401 (usados pelas telas)

FORMATO_UTC = "%Y-%m-%d %H:%M:%S"  # como as datas/horas ficam gravadas no banco (sempre UTC)


# ---------------------------------------------------------------------------
# Relógio
# ---------------------------------------------------------------------------

def fuso():
    return ZoneInfo(current_app.config["FUSO_HORARIO"])


def agora_local():
    return datetime.now(fuso())


def agora_utc():
    return datetime.now(timezone.utc)


def para_texto_utc(momento):
    return momento.astimezone(timezone.utc).strftime(FORMATO_UTC)


def de_texto_utc(texto):
    return datetime.strptime(texto, FORMATO_UTC).replace(tzinfo=timezone.utc)


def inicio_do_dia_utc(dia):
    """00:00 do dia (no fuso da loja) convertido para texto UTC."""
    return para_texto_utc(datetime(dia.year, dia.month, dia.day, tzinfo=fuso()))


def tempo_desde(texto_utc):
    """'agora há pouco', 'há 5 min', 'há 2 h', 'há 3 dias'."""
    if not texto_utc:
        return "nunca"
    segundos = int((agora_utc() - de_texto_utc(texto_utc)).total_seconds())
    if segundos < 60:
        return "agora há pouco"
    if segundos < 3600:
        return f"há {segundos // 60} min"
    if segundos < 86400:
        return f"há {segundos // 3600} h"
    return f"há {segundos // 86400} dia(s)"


def local_formatado(texto_utc):
    if not texto_utc:
        return "-"
    return de_texto_utc(texto_utc).astimezone(fuso()).strftime("%d/%m/%Y %H:%M")


def duracao_formatada(segundos):
    segundos = int(segundos or 0)
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas} h {minutos:02d} min"
    if minutos:
        return f"{minutos} min {seg:02d} s"
    return f"{seg} s"


# ---------------------------------------------------------------------------
# Agendamento
# ---------------------------------------------------------------------------

def _no_horario(hora, inicio, fim):
    inicio = inicio or "00:00"
    fim = fim or "24:00"
    if inicio <= fim:
        return inicio <= hora < fim
    # Faixa que vira a noite, ex.: 22:00 até 02:00.
    return hora >= inicio or hora < fim


def situacao(item, agora):
    """Retorna (código, texto) dizendo se a propaganda está no ar agora e por quê.

    Os dias da semana valem para o dia do relógio: numa faixa 22:00-02:00
    marcada só na sexta, a parte depois da meia-noite cai no sábado.
    """
    hoje = agora.date().isoformat()
    if not item["ativo"]:
        return "inativa", "Inativa"
    if item["inicio"] and hoje < item["inicio"]:
        return "agendada", "Começa em " + datetime.fromisoformat(item["inicio"]).strftime("%d/%m/%Y")
    if item["fim"] and hoje > item["fim"]:
        return "encerrada", "Encerrada"
    if str(agora.weekday()) not in (item["dias_semana"] or TODOS_OS_DIAS):
        return "fora_do_dia", "Fora do dia"
    if not _no_horario(agora.strftime("%H:%M"), item["hora_inicio"], item["hora_fim"]):
        return "fora_do_horario", "Fora do horário"
    return "no_ar", "No ar agora"


def esta_no_ar(item, agora):
    return situacao(item, agora)[0] == "no_ar"


def offset_minutos(dia):
    """Diferença do fuso para UTC em minutos (ex.: -180 em São Paulo)."""
    return int(datetime(dia.year, dia.month, dia.day, 12, tzinfo=fuso()).utcoffset() / timedelta(minutes=1))
