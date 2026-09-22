from test_api import new_chat


def test_rename_pin_search_export_owner(client, auth):
    alice, bob = auth(), auth("bob@example.com")
    first, second = new_chat(client, alice), new_chat(client, alice)
    assert (
        client.patch(
            f"/chats/{first}", headers=alice, json={"title": "Pinned 100%", "pinned": True}
        ).status_code
        == 200
    )
    assert client.get("/chats", headers=alice).json()[0]["id"] == first
    assert [r["id"] for r in client.get("/chats?q=100%25", headers=alice).json()] == [first]
    assert client.patch(f"/chats/{first}", headers=bob, json={"title": "attack"}).status_code == 404
    client.post(f"/chats/{first}/stream", headers=alice, json={"content": "Private text"})
    assert client.get(f"/chats/{first}/export", headers=bob).status_code == 404
    assert client.get("/chats/export", headers=bob).json() == []
    exported = client.get("/chats/export", headers=alice).json()
    assert {row["chat"]["id"] for row in exported} == {first, second}
    assert "Private text" in client.get(f"/chats/{first}/export?format=markdown", headers=alice).text
    # The markdown header must name the roles in readable Russian, not mojibake.
    markdown = client.get(f"/chats/{first}/export?format=markdown", headers=alice).text
    assert "## Вы ·" in markdown and "Р’С‹" not in markdown
    assert "## Canalla LLM ·" in markdown


def test_edit_resend_and_regenerate_truncate_linear_history(client, auth):
    alice, bob = auth(), auth("bob@example.com")
    chat = new_chat(client, alice)
    for content in ["First", "Second"]:
        assert (
            client.post(f"/chats/{chat}/stream", headers=alice, json={"content": content}).status_code == 200
        )
    rows = client.get(f"/chats/{chat}/messages", headers=alice).json()
    message = rows[0]["id"]
    path = f"/chats/{chat}/messages/{message}"
    assert client.patch(path, headers=bob, json={"content": "Attack"}).status_code == 404
    assert client.patch(path, headers=alice, json={"content": "Edited"}).status_code == 200
    assert len(client.get(f"/chats/{chat}/messages", headers=alice).json()) == 4
    assert client.post(path + "/resend", headers=alice, json={"content": "Replaced"}).status_code == 200
    replaced = client.get(f"/chats/{chat}/messages", headers=alice).json()
    assert len(replaced) == 2 and replaced[0]["content"] == "Replaced"
    assert replaced[0]["edited_at"]
    assert (
        client.post(f"/chats/{chat}/messages/{replaced[1]['id']}/regenerate", headers=alice).status_code
        == 200
    )
    regenerated = client.get(f"/chats/{chat}/messages", headers=alice).json()
    assert len(regenerated) == 2 and regenerated[1]["id"] != replaced[1]["id"]
    assert regenerated[1]["status"] == "complete"
    assert client.get("/compute/usage/me", headers=alice).json()["periods"]["all"]["requests"] == 4
