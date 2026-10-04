"""Regras do ponto sem banco: horário (inclusive o turno que vira a noite), QR da loja, entrada e saída."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.domain.erros import NaoEncontrado, SemPermissao
from src.domain.horario import Horario, HorarioInvalido, ler_dias, ler_hora, resumo_dias
from src.domain.ponto import (
    CodigoErrado,
    ErroDePonto,
    FaltaLerQr,
    ForaDoHorario,
    Funcionario,
    MuitasTentativas,
    QrDoPonto,
    QrJaUsado,
    QrVencido,
    RegistroDePonto,
    ServicoDePonto,
)
from src.domain.ponto.qr import INVALIDO, USADO, VALIDO
from src.domain.tentativas import LimiteDeTentativas

FUSO = ZoneInfo("America/Sao_Paulo")
SEXTA_10H = datetime(2026, 10, 2, 10, 0, tzinfo=FUSO)   # 2026-10-02 é uma sexta-feira


def em(dia, hora):
    return datetime.fromisoformat(f"2026-10-{dia:02d}T{hora}").replace(tzinfo=FUSO)


# -- horário ------------------------------------------------------------------------------

def test_turno_que_vira_a_noite_pertence_ao_dia_em_que_comeca():
    sexta_18_as_2 = Horario("4", "18:00", "02:00")
    assert sexta_18_as_2.vale(em(2, "19:00")) and sexta_18_as_2.vale(em(3, "01:30"))
    assert not sexta_18_as_2.vale(em(3, "19:00")) and not sexta_18_as_2.vale(em(2, "01:30"))
    assert not sexta_18_as_2.vale(em(2, "10:00"))
    dia_todo = Horario("4")
    assert dia_todo.vale(em(2, "03:00")) and not dia_todo.vale(em(3, "03:00"))
    comercial = Horario("01234", "08:00", "18:00")
    assert comercial.vale(em(2, "08:00")) and not comercial.vale(em(2, "18:00"))


def test_horario_digitado():
    assert Horario.novo("01234", "08:00", "17:00").resumo == "Seg a Sex, 08:00 às 17:00"
    assert Horario.novo("56", None, None).resumo == "Sáb e Dom, o dia todo"
    assert resumo_dias("") == "Todos os dias" and resumo_dias("024") == "Seg, Qua, Sex"
    for dias, inicio, fim in (("", None, None), ("1", "08:00", None), ("1", "08:00", "08:00")):
        with pytest.raises(HorarioInvalido):
            Horario.novo(dias, inicio, fim)
    assert (ler_hora("8:05"), ler_hora("25:00"), ler_hora(""), ler_hora(None)) == ("08:05", None, None, None)
    assert ler_dias(["6", "0", "x", "0"]) == "06"


# -- QR code da loja -------------------------------------------------------------------------

def test_qr_muda_a_cada_dois_minutos_com_tolerancia_de_30_segundos():
    qr, inicio = QrDoPonto(1, "segredo"), 1_000_080.0   # início de uma janela de 2 minutos
    token = qr.token(0, inicio + 100)
    assert qr.situacao(token, 0, inicio + 119) == VALIDO
    assert qr.situacao(token, 0, inicio + 140) == VALIDO      # acabou de sair da tela
    assert qr.situacao(token, 0, inicio + 160) == INVALIDO
    assert qr.situacao(qr.token(0, inicio + 240), 0, inicio + 100) == INVALIDO   # do futuro
    assert qr.situacao(token, 1, inicio + 100) == USADO       # alguém já leu (a geração subiu)


def test_qr_falso_ou_de_outra_loja_nao_vale():
    qr, agora = QrDoPonto(1, "segredo"), 1_000_000.0
    token = qr.token(0, agora)
    assert QrDoPonto(2, "segredo").situacao(token, 0, agora) == INVALIDO
    assert QrDoPonto(1, "outro").situacao(token, 0, agora) == INVALIDO
    assert qr.situacao("lixo", 0, agora) == INVALIDO
    assert qr.situacao(token[:-1] + ("0" if token[-1] != "0" else "1"), 0, agora) == INVALIDO


def test_codigo_digitavel_corresponde_ao_qr():
    qr, agora = QrDoPonto(1, "segredo"), 1_000_000.0
    token = qr.token(3, agora)
    codigo = qr.codigo_do_token(token)
    assert len(codigo) == 6 and "0" not in codigo and "O" not in codigo
    assert qr.token_do_codigo(codigo.lower(), 3, agora) == token
    assert qr.token_do_codigo(codigo, 4, agora) is None    # já usado
    assert qr.token_do_codigo("ABC", 3, agora) is None
    assert qr.codigo_do_token("lixo") is None


# -- serviço ------------------------------------------------------------------------------------

class Relogio:
    def __init__(self, momento=SEXTA_10H):
        self.agora = momento.astimezone(timezone.utc)

    def __call__(self):
        return self.agora

    def andar(self, **tempo):
        self.agora += timedelta(**tempo)


class RepositorioFalso:
    empresa_id = 1

    def __init__(self, pessoas):
        self.configs = {"ponto_ativo": "1"}
        self.pessoas = {p.id: p for p in pessoas}
        self.registros_ = []
        self.sessoes_encerradas = []

    def config(self, chave):
        return self.configs.get(chave)

    def gravar_config(self, chave, valor):
        self.configs[chave] = valor

    def trocar_geracao(self, de, para):
        if int(self.configs.get("ponto_qr_geracao", "0")) != de:
            return False
        self.configs["ponto_qr_geracao"] = str(para)
        return True

    def funcionario(self, usuario_id):
        return self.pessoas.get(usuario_id)

    def funcionarios_com_ponto_aberto(self):
        return [self.pessoas[r.usuario_id] for r in self.registros_ if r.saida is None]

    def gravar_horario(self, usuario_id, horario, exige_ponto):
        from dataclasses import replace
        self.pessoas[usuario_id] = replace(self.pessoas[usuario_id], horario=horario, exige_ponto=exige_ponto)

    def encerrar_sessoes(self, usuario_id):
        self.sessoes_encerradas.append(usuario_id)

    def aberto(self, usuario_id):
        return next((r for r in self.registros_ if r.usuario_id == usuario_id and r.saida is None), None)

    def abrir(self, funcionario, entrada, ip):
        self.registros_.append(RegistroDePonto(len(self.registros_) + 1, funcionario.id, funcionario.nome, entrada))
        return True

    def fechar(self, usuario_id, saida, motivo, encerrado_por):
        from dataclasses import replace
        for i, r in enumerate(self.registros_):
            if r.usuario_id == usuario_id and r.saida is None:
                self.registros_[i] = replace(r, saida=saida, motivo_saida=motivo)
                return 1
        return 0

    def registros(self, de, ate, usuario_id):
        return [r for r in reversed(self.registros_)
                if de <= r.entrada < ate and (usuario_id is None or r.usuario_id == usuario_id)]


ADMIN = Funcionario(1, "admin", "admin")
JOAO = Funcionario(2, "joao", "garcom", horario=Horario("01234", "08:00", "18:00"))
ANA = Funcionario(3, "ana", "caixa")
ISENTA = Funcionario(4, "isenta", "caixa", exige_ponto=False)
SUPORTE = Funcionario(5, "suporte", "admin", plataforma=True)


def montar(momento=SEXTA_10H):
    repo, relogio = RepositorioFalso([ADMIN, JOAO, ANA, ISENTA, SUPORTE]), Relogio(momento)
    tentativas = LimiteDeTentativas(5, 600, relogio=lambda: relogio().timestamp())
    return ServicoDePonto(repo, FUSO, relogio=relogio, tentativas=tentativas), repo, relogio


def test_quem_bate_ponto():
    ponto, repo, _ = montar()
    assert ponto.bate_ponto(JOAO) and ponto.bate_ponto(ANA)
    assert not ponto.bate_ponto(ADMIN) and not ponto.bate_ponto(ISENTA) and not ponto.bate_ponto(SUPORTE)
    ponto.ligar(False)
    assert not ponto.bate_ponto(JOAO)


def test_entrada_pede_o_qr_e_o_horario():
    ponto, _, relogio = montar()
    with pytest.raises(FaltaLerQr):
        ponto.registrar_entrada(JOAO, leu_o_qr=False)
    assert ponto.registrar_entrada(JOAO, leu_o_qr=True)
    assert not ponto.registrar_entrada(JOAO, leu_o_qr=True)   # já estava aberto
    ponto.exigir_qr(False)
    assert ponto.registrar_entrada(ANA, leu_o_qr=False)        # QR dispensado: só o botão
    relogio.andar(hours=9)                                      # 19:00: fora do horário do João
    ponto.registrar_saida(JOAO, leu_o_qr=True)
    with pytest.raises(ForaDoHorario, match="Seg a Sex, 08:00 às 18:00"):
        ponto.registrar_entrada(JOAO, leu_o_qr=True)


def test_saida_sem_qr_fica_anotada():
    ponto, repo, relogio = montar()
    ponto.registrar_entrada(JOAO, leu_o_qr=True)
    relogio.andar(hours=2, minutes=30)
    assert ponto.registrar_saida(JOAO, leu_o_qr=False)
    assert not ponto.registrar_saida(JOAO, leu_o_qr=False)   # nada mais aberto
    [registro] = ponto.historico(SEXTA_10H - timedelta(days=1), SEXTA_10H + timedelta(days=1))
    assert (registro.motivo_saida, registro.segundos) == ("saída sem QR code", 9000)


def test_ponto_fecha_quando_o_horario_acaba():
    ponto, _, relogio = montar()
    ponto.registrar_entrada(JOAO, leu_o_qr=True)
    ponto.registrar_entrada(ANA, leu_o_qr=True)
    assert ponto.conferir_expediente(JOAO)[1] is False
    relogio.andar(hours=8, minutes=1)                         # 18:01
    assert ponto.fechar_fora_do_horario() == ["joao"]          # a Ana trabalha o dia todo
    assert ponto.conferir_expediente(JOAO) == (None, False)
    assert ponto.aberto(ANA.id) is not None


def test_conferir_expediente_fecha_na_hora():
    ponto, repo, relogio = montar()
    ponto.registrar_entrada(JOAO, leu_o_qr=True)
    relogio.andar(hours=8)
    assert ponto.conferir_expediente(JOAO) == (None, True)
    assert repo.registros_[0].motivo_saida == "fim do horário"
    relogio.andar(days=-1)
    assert ponto.historico(SEXTA_10H, SEXTA_10H + timedelta(days=1))[0].segundos == 8 * 3600


def test_qr_lido_uma_vez_so_e_codigo_digitado():
    ponto, _, relogio = montar()
    token = ponto.token_atual()
    ponto.usar_token(token)
    with pytest.raises(QrJaUsado):
        ponto.usar_token(token)
    novo = ponto.token_atual()
    assert novo != token
    relogio.andar(minutes=5)
    with pytest.raises(QrVencido):
        ponto.usar_token(novo)
    ponto.usar_codigo(ponto.codigo_digitavel(ponto.token_atual()), quem=2)
    assert ponto.geracao == 2


def test_muitos_codigos_errados_bloqueiam_por_um_tempo():
    ponto, _, relogio = montar()
    for _ in range(5):
        with pytest.raises(CodigoErrado):
            ponto.usar_codigo("ZZZZZZ", quem=2)
    certo = ponto.codigo_digitavel(ponto.token_atual())
    with pytest.raises(MuitasTentativas):
        ponto.usar_codigo(certo, quem=2)
    ponto.usar_codigo(certo, quem=3)          # o bloqueio é só de quem errou
    relogio.andar(minutes=11)
    ponto.usar_codigo(ponto.codigo_digitavel(ponto.token_atual()), quem=2)


def test_endereco_do_quiosque_e_segredo_ficam_gravados():
    ponto, repo, _ = montar()
    endereco = ponto.codigo_quiosque()
    assert ponto.codigo_quiosque() == endereco
    assert ponto.codigo_quiosque(novo=True) != endereco
    token = ponto.token_atual()
    assert repo.configs["ponto_segredo"] and ponto.situacao(token) == VALIDO


def test_horario_salvo_e_desconectar():
    ponto, repo, _ = montar()
    ponto.registrar_entrada(ANA, leu_o_qr=True)
    with pytest.raises(HorarioInvalido, match="“ana”: escolha pelo menos um dia"):
        ponto.salvar_horario(ANA.id, "", None, None, isento=False)
    assert ponto.salvar_horario(ANA.id, "56", "10:00", "16:00", isento=True).nome == "ana"
    assert repo.pessoas[ANA.id].horario.dias == "56" and not repo.pessoas[ANA.id].exige_ponto
    with pytest.raises(NaoEncontrado):
        ponto.salvar_horario(99, "1", None, None, isento=False)
    with pytest.raises(ErroDePonto, match="botão Sair"):
        ponto.desconectar(ADMIN.id, ADMIN)
    with pytest.raises(SemPermissao):
        ponto.desconectar(SUPORTE.id, ADMIN)
    ponto.desconectar(ANA.id, ADMIN)
    assert repo.sessoes_encerradas == [ANA.id] and ponto.aberto(ANA.id) is None
    assert repo.registros_[0].motivo_saida == "desconectado pelo administrador"


def test_totais_por_pessoa():
    registros = [RegistroDePonto(1, 2, "joao", SEXTA_10H, segundos=100), RegistroDePonto(2, 3, "ana", SEXTA_10H, segundos=50),
                 RegistroDePonto(3, 2, "joao", SEXTA_10H, segundos=20)]
    assert ServicoDePonto.totais(registros) == [("ana", 50), ("joao", 120)]
