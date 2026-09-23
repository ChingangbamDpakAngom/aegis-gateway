from fastapi.testclient import TestClient
from gateway.api.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {'status': "OK"}

def test_chat_request():
    response = client.post(
        "/chat",
        json={
            "user_id": "automated-test-user",
            "message": "Hello from pytest",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "echo": "Hello from pytest",
        "from": "automated-test-user",
    }


def test_chat_validation():
    response = client.post(
        "/chat",
        json={
            "user_id": "automated-test-user",
        },
    )

    assert response.status_code == 422