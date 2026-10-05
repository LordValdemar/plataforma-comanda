"""A tela da cozinha sem banco: itens agrupados por comanda, o que falta fazer no alto."""

from src.domain.comanda.cozinha import agrupar_por_comanda, entregues


def item(id, comanda, status, lancado="10:00", mesa=None, garcom=None):
    return {"id": id, "comanda_id": comanda, "numero": comanda * 10, "mesa": mesa, "nome": f"Item {id}", "quantidade": 1,
            "observacao": None, "status": status, "lancado_em": lancado, "garcom": garcom}


def test_agrupa_e_poe_as_prontas_no_fim():
    itens = [item(1, 1, "pronto"), item(2, 2, "pendente", "10:05", mesa="4", garcom="ana"), item(3, 1, "pronto"),
             item(4, 3, "preparando", "10:07")]
    grupos = agrupar_por_comanda(itens, hora=lambda t: t, minutos_desde=lambda t: int(t[-2:]))
    assert [(g["comanda_id"], g["tudo_pronto"]) for g in grupos] == [(2, False), (3, False), (1, True)]
    assert [i["id"] for i in grupos[2]["itens"]] == [1, 3]
    assert grupos[0]["mesa"] == "4" and grupos[0]["itens"][0]["garcom"] == "ana" and grupos[0]["minutos"] == 5
    assert grupos[1]["itens"][0] == {"id": 4, "nome": "Item 4", "quantidade": 1, "observacao": "", "status": "preparando",
                                     "hora": "10:07", "minutos": 7, "garcom": ""}
    assert agrupar_por_comanda([], str, len) == []


def test_entregues():
    assert entregues([{"id": 1, "nome": "X", "quantidade": 2, "numero": 5, "mesa": None, "atualizado_em": "t"}],
                     hora=lambda t: "12:00") == [{"id": 1, "nome": "X", "quantidade": 2, "numero": 5, "mesa": "",
                                                  "hora": "12:00"}]
