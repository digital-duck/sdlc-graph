# verifies: REQ-001
from cart import Cart


def test_add():
    c = Cart()
    c.add("a", 2)
    assert c.count() == 2
