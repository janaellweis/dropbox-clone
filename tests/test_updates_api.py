import uuid


def test_updates_pagination(api_client):
    for index in range(3):
        response = api_client.post(
            "/files",
            data={
                "key": f"journal-page-{index}.txt",
                "request_id": str(uuid.uuid4()),
            },
            files={"file": (f"journal-page-{index}.txt", b"Hello")},
        )
        assert response.status_code == 200

    first_page = api_client.get("/updates", params={"since": 0, "limit": 2})
    assert first_page.status_code == 200
    assert len(first_page.json()["updates"]) == 2
    assert first_page.json()["has_more"] is True

    checkpoint = first_page.json()["next_checkpoint"]

    second_page = api_client.get(
        "/updates",
        params={"since": checkpoint, "limit": 2},
    )

    assert second_page.status_code == 200
    assert len(second_page.json()["updates"]) == 1
    assert second_page.json()["has_more"] is False

    first_ids = [event["id"] for event in first_page.json()["updates"]]
    second_ids = [event["id"] for event in second_page.json()["updates"]]

    assert first_ids + second_ids == sorted(first_ids + second_ids)

    assert set(first_ids).isdisjoint(second_ids)


def test_updates_checkpoint_returns_only_new_changes(api_client):
    first = api_client.post(
        "/files",
        data={"key": "checkpoint-first.txt", "request_id": str(uuid.uuid4())},
        files={"file": ("checkpoint-first.txt", b"First")},
    )
    assert first.status_code == 200

    initial = api_client.get("/updates")
    assert initial.status_code == 200

    checkpoint = initial.json()["next_checkpoint"]

    second = api_client.post(
        "/files",
        data={"key": "checkpoint-second.txt", "request_id": str(uuid.uuid4())},
        files={"file": ("checkpoint-second.txt", b"Second")},
    )
    assert second.status_code == 200

    response = api_client.get("/updates", params={"since": checkpoint})

    assert response.status_code == 200
    assert len(response.json()["updates"]) == 1
    assert response.json()["updates"][0]["path"] == "checkpoint-second.txt"


def test_updates_exact_page_size_has_no_more(api_client):
    for index in range(2):
        response = api_client.post(
            "/files",
            data={
                "key": f"exact-page-{index}.txt",
                "request_id": str(uuid.uuid4()),
            },
            files={"file": (f"exact-page-{index}.txt", b"Hello")},
        )
        assert response.status_code == 200

    response = api_client.get(
        "/updates",
        params={"since": 0, "limit": 2},
    )

    assert response.status_code == 200
    assert len(response.json()["updates"]) == 2
    assert response.json()["has_more"] is False
