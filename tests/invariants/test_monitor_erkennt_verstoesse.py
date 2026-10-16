"""Gegenprobe: der Invariantenprüfer erkennt jede Verletzungsart aus Kap. 7.5."""
import pytest

from orderflow.verifier.invariants import InvariantMonitor, InvariantViolation


def view(state="IDLE", position=None, orders=(), fills=(), pos=0):
    return {"engine": {"state": state, "position": position}, "orders": list(orders), "fills": list(fills),
            "position_from_fills": pos}


def order(cid, kind="ENTRY", side="SELL", qty=1, status="FILLED", created=0):
    return {"client_order_id": cid, "kind": kind, "side": side, "quantity": qty, "status": status, "created_at": created}


def fill(cid, side="SELL", qty=1, ts=10):
    return {"client_order_id": cid, "side": side, "quantity": qty, "ts": ts}


CASES = {
    "nie_Long": view(orders=[order("a", side="BUY")]),
    "nie_mehr_als_ein_Kontrakt": view(orders=[order("a", qty=2, status="WORKING")]),
    "keine_Order_ohne_ID": view(orders=[order("", status="WORKING")]),
    "kein_zweiter_Entry_bei_offenem_Entry": view(orders=[order("a", status="WORKING"), order("b", status="WORKING")]),
    "kein_Entry_bei_Position": view("OPEN", {"quantity": 1}, [order("a"), order("b", status="WORKING", created=20)],
                                    [fill("a")], -1),
    "kein_neuer_Trade_nach_erstem_Fill": view("DONE_FOR_SESSION", None,
                                              [order("a"), order("x", "EXIT", "BUY"), order("b", status="EXPIRED", created=50)],
                                              [fill("a"), fill("x", "BUY", ts=30)], 0),
    "kein_Exit_nach_Glattstellung": view("DONE_FOR_SESSION", None,
                                         [order("a"), order("x", "EXIT", "BUY"), order("y", "EXIT", "BUY", status="WORKING")],
                                         [fill("a"), fill("x", "BUY", ts=30)], 0),
    "kein_Positionsverlust": view("IDLE", None, [order("a")], [fill("a")], -1),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_verstoss_wird_erkannt(name):
    with pytest.raises(InvariantViolation):
        InvariantMonitor().check(CASES[name], name)


def test_gueltiger_zustand_passiert():
    InvariantMonitor().check(view("OPEN", {"quantity": 1}, [order("a")], [fill("a")], -1))
