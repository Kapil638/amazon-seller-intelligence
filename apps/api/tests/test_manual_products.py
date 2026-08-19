from fastapi.testclient import TestClient

VALID_MANUAL_PRODUCT = {
    "asin": "B0ABCDE123",
    "title": "Himalayan Pink Salt Grinder, 200g",
    "brand": "Saffron Peak",
    "price": 349,
    "currency": "INR",
    "rating": 4.3,
    "review_count": 512,
    "category": "Grocery & Gourmet Foods",
    "bsr_rank": 1204,
    "bsr_category": "Grocery & Gourmet Foods",
    "availability": "In Stock",
    "seller": "Saffron Peak Retail",
    "description": "A fictional listing used to test manual product input.",
    "bullet_points": ["Ceramic grinder mechanism", "Refillable 200g jar"],
    "image_urls": ["https://placehold.co/800x800/7a2e1f/ffffff?text=Salt+Grinder"],
    "marketplace": "amazon.in",
}


def test_valid_manual_product(client: TestClient) -> None:
    response = client.post("/api/v1/products/manual", json=VALID_MANUAL_PRODUCT)
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source"] == "manual"
    product = body["product"]
    assert product["asin"] == "B0ABCDE123"
    assert product["title"] == "Himalayan Pink Salt Grinder, 200g"
    assert product["brand"] == "Saffron Peak"
    assert product["price"]["amount"] == 349
    assert product["price"]["currency"] == "INR"
    assert product["rating"] == 4.3
    assert product["review_count"] == 512
    assert product["category"] == "Grocery & Gourmet Foods"
    assert product["bsr"]["rank"] == 1204
    assert product["availability"] == "In Stock"
    assert product["seller"]["name"] == "Saffron Peak Retail"
    assert product["bullet_points"] == [
        "Ceramic grinder mechanism",
        "Refillable 200g jar",
    ]
    assert product["images"][0]["url"].startswith("https://placehold.co/")
    assert product["marketplace"] == "amazon.in"
    assert product["last_fetched_at"]


def test_manual_asin_normalized_to_uppercase(client: TestClient) -> None:
    payload = {**VALID_MANUAL_PRODUCT, "asin": "b0abcde123"}
    response = client.post("/api/v1/products/manual", json=payload)
    assert response.status_code == 200
    assert response.json()["product"]["asin"] == "B0ABCDE123"


def test_manual_invalid_asin(client: TestClient) -> None:
    payload = {**VALID_MANUAL_PRODUCT, "asin": "not-an-asin"}
    response = client.post("/api/v1/products/manual", json=payload)
    assert response.status_code == 400
    assert "asin" in response.json()["detail"].lower()


def test_manual_missing_title(client: TestClient) -> None:
    payload = {k: v for k, v in VALID_MANUAL_PRODUCT.items() if k != "title"}
    response = client.post("/api/v1/products/manual", json=payload)
    assert response.status_code == 400
    assert "title" in response.json()["detail"].lower()


def test_manual_invalid_rating_above_five(client: TestClient) -> None:
    payload = {**VALID_MANUAL_PRODUCT, "rating": 5.5}
    response = client.post("/api/v1/products/manual", json=payload)
    assert response.status_code == 400
    assert "rating" in response.json()["detail"].lower()


def test_manual_negative_price(client: TestClient) -> None:
    payload = {**VALID_MANUAL_PRODUCT, "price": -10}
    response = client.post("/api/v1/products/manual", json=payload)
    assert response.status_code == 400
    assert "price" in response.json()["detail"].lower()


def test_manual_negative_review_count(client: TestClient) -> None:
    payload = {**VALID_MANUAL_PRODUCT, "review_count": -1}
    response = client.post("/api/v1/products/manual", json=payload)
    assert response.status_code == 400
    assert "review_count" in response.json()["detail"].lower()
