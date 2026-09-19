from app.core.security import create_access_token, hash_password, verify_password


def test_password_hash_roundtrip():
    stored = hash_password("supersecret1")
    assert stored != "supersecret1"
    assert verify_password("supersecret1", stored)
    assert not verify_password("wrong-password", stored)


def test_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


def test_register_returns_user_without_password(client, credentials):
    response = client.post("/api/auth/register", json=credentials)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == credentials["email"]
    assert body["is_active"] is True
    assert "password" not in body and "hashed_password" not in body


def test_duplicate_email_is_rejected(client, credentials):
    client.post("/api/auth/register", json=credentials)
    assert client.post("/api/auth/register", json=credentials).status_code == 409


def test_short_password_is_rejected(client):
    response = client.post(
        "/api/auth/register", json={"email": "a@b.com", "password": "short"}
    )
    assert response.status_code == 422


def test_login_returns_bearer_token(client, credentials):
    client.post("/api/auth/register", json=credentials)
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"].count(".") == 2


def test_login_with_wrong_password(client, credentials):
    client.post("/api/auth/register", json=credentials)
    response = client.post(
        "/api/auth/login", json={**credentials, "password": "not-the-password"}
    )
    assert response.status_code == 401


def test_login_with_unknown_email(client):
    response = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "supersecret1"}
    )
    assert response.status_code == 401


def test_me_requires_a_token(client):
    assert client.get("/api/users/me").status_code == 401


def test_me_returns_the_logged_in_user(client, auth_headers, credentials):
    response = client.get("/api/users/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["email"] == credentials["email"]


def test_tampered_token_is_rejected(client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{'a' * len(signature)}"
    response = client.get("/api/users/me", headers={"Authorization": f"Bearer {tampered}"})
    assert response.status_code == 401


def test_expired_token_is_rejected(client, auth_headers):
    expired = create_access_token(1, expires_minutes=-1)
    response = client.get("/api/users/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_update_me(client, auth_headers):
    response = client.patch("/api/users/me", json={"full_name": "Ada"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["full_name"] == "Ada"


def test_form_login_for_swagger(client, credentials):
    """The Authorize button posts form data with the email in `username`."""
    client.post("/api/auth/register", json=credentials)
    response = client.post(
        "/api/auth/token",
        data={"username": credentials["email"], "password": credentials["password"]},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == credentials["email"]


def test_form_login_with_wrong_password(client, credentials):
    client.post("/api/auth/register", json=credentials)
    response = client.post(
        "/api/auth/token", data={"username": credentials["email"], "password": "nope"}
    )
    assert response.status_code == 401
