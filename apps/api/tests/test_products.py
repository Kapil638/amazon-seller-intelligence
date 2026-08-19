from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_valid_mock_asin(client: TestClient) -> None:
    response = client.get("/api/v1/products/B0TEST0001?marketplace=amazon.in")
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source"] == "mock"
    data = body["product"]
    assert data["asin"] == "B0TEST0001"
    assert data["marketplace"] == "amazon.in"
    assert data["title"]
    assert data["brand"] == "Lumora Wellness"
    assert data["price"]["currency"] == "INR"
    assert data["price"]["amount"] > 0
    assert data["rating"] is not None
    assert data["review_count"] is not None
    assert data["category"] == "Health & Personal Care"
    assert data["bsr"]["rank"] >= 1
    assert data["availability"]
    assert len(data["bullet_points"]) > 0
    assert len(data["images"]) > 0


def test_asin_is_normalized_to_uppercase(client: TestClient) -> None:
    response = client.get("/api/v1/products/b0test0002")
    assert response.status_code == 200
    assert response.json()["product"]["asin"] == "B0TEST0002"
    assert response.json()["meta"]["source"] == "mock"


def test_invalid_asin_format(client: TestClient) -> None:
    response = client.get("/api/v1/products/not-an-asin")
    assert response.status_code == 400
    assert "Invalid ASIN" in response.json()["detail"]


def test_invalid_asin_too_short(client: TestClient) -> None:
    response = client.get("/api/v1/products/B0TEST001")
    assert response.status_code == 400


def test_unknown_asin(client: TestClient) -> None:
    response = client.get("/api/v1/products/B0TEST9999?marketplace=amazon.in")
    assert response.status_code == 404
    assert "B0TEST9999" in response.json()["detail"]


def test_unsupported_marketplace(client: TestClient) -> None:
    response = client.get("/api/v1/products/B0TEST0001?marketplace=amazon.com")
    assert response.status_code == 400
    assert "Unsupported marketplace" in response.json()["detail"]
