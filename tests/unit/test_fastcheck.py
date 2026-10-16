"""Der schnelle Vorabtest darf nie etwas akzeptieren, das das JSON-Schema ablehnt."""
import copy
import random

from orderflow.common import schema
from orderflow.dataadapter.fastcheck import fast_valid
from orderflow.replay.synthetic import random_session
from tests.scenarios import DAY, POLICY
from tests.unit.test_schemas import EVENT_5_1

WRONG = [None, -1, 0, 1.5, "", "x", True, [], {}, "2026-09-22T14:00:00Z", "BUY", "MBO", "NEW_TOTAL"]


def _variants(rec, rng):
    yield rec
    for k in rec:
        yield {x: v for x, v in rec.items() if x != k}
        for w in rng.sample(WRONG, 4):
            yield dict(rec, **{k: w})
    for k in ("order_id", "text", "corrects_event_id", "market_status", "book_type", "bid_ticks", "extra"):
        for w in rng.sample(WRONG, 3):
            yield dict(rec, **{k: w})


def test_fastcheck_aequivalenz():
    rng = random.Random(7)
    base = random_session(DAY, POLICY, 6).records()
    samples = rng.sample(base, 40) + [EVENT_5_1]
    book = {k: v for k, v in EVENT_5_1.items() if k not in ("size_contracts", "aggressor", "classification_method")}
    book.update(event_type="BOOK", book_side="ASK", action="DELETE", quantity=0, quantity_semantics="CHANGE",
                book_type="MBO", order_id="o1")
    bust = {k: v for k, v in EVENT_5_1.items() if k not in ("price_ticks", "size_contracts", "aggressor",
                                                            "classification_method")}
    bust.update(event_type="TRADE_BUST", corrects_event_id="t1")
    samples += [book, bust]
    checked = disagreements = 0
    for rec in samples:
        for v in _variants(copy.deepcopy(rec), rng):
            fast = fast_valid(v)
            full = not schema.errors(v, "market_event")
            assert not (fast and not full), v          # nie zu großzügig
            disagreements += fast != full
            checked += 1
    assert checked > 2000
    assert disagreements == 0
