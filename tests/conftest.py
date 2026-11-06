import os

# Must be set before tasks.py is imported: use an in-memory queue instead of Redis.
os.environ["DRAMATIQ_BROKER"] = "stub"

import fakeredis  # noqa: E402
import pytest  # noqa: E402

import store  # noqa: E402
from main import app  # noqa: E402
from tasks import broker  # noqa: E402


@pytest.fixture(autouse=True)
def fake_redis():
    store.set_client(fakeredis.FakeRedis())
    broker.flush_all()
    yield


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
