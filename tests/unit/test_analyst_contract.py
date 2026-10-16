from orderflow.analyst.interface import AnalystView, accept_reviews
from orderflow.journal.journal import Journal
from tests.unit.test_schemas import REVIEW_13_3


def test_analyst_sieht_nur_freigegebene_eintraege_schreibgeschuetzt():
    j = Journal()
    j.append("DECISION", 1, {"x": 1})
    j.append("ORDER", 2, {"client_order_id": "c"})
    j.append("FILL", 3, {"price_ticks": 1})
    view = AnalystView.from_journal("S", j)
    assert [r["kind"] for r in view.records] == ["DECISION"]
    try:
        view.records[0]["kind"] = "FILL"
        raise AssertionError("Sicht ist nicht schreibgeschützt")
    except TypeError:
        pass


def test_lernprotokolle_werden_validiert():
    ok, rejected = accept_reviews([REVIEW_13_3, dict(REVIEW_13_3, error_class="MARKT_IRRATIONAL"),
                                   dict(REVIEW_13_3, status="VALIDATED_WITH_SCOPE")])
    assert ok == [REVIEW_13_3]
    assert len(rejected) == 2
