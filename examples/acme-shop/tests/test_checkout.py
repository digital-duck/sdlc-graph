# verifies: REQ-002
from cart import Cart
from checkout import total


def test_total():
    c = Cart()
    c.add("a", 2)
    assert total(c, {"a": 5.0}) == 10.0
