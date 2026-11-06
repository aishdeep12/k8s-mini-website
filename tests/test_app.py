def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_info_has_fields(client):
    data = client.get("/api/info").get_json()
    assert {"version", "pod", "node", "started_at"} <= data.keys()


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Served by pod" in r.data
    assert b"OCR an image" in r.data
