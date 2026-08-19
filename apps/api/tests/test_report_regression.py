from fastapi.testclient import TestClient


def test_existing_asin_listing_and_competitor_flows(client: TestClient) -> None:
    product_response = client.get("/api/v1/products/B0TEST0001")
    assert product_response.status_code == 200
    product = product_response.json()["product"]

    listing = client.post(
        "/api/v1/analysis/listing",
        json={"product": product, "source": "mock"},
    )
    assert listing.status_code == 200
    assert listing.json()["meta"]["engine"] == "deterministic"

    comparison = client.post(
        "/api/v1/analysis/competitors",
        json={
            "target_product": product,
            "competitor_asins": ["B0TEST0002"],
            "source": "mock",
        },
    )
    assert comparison.status_code == 200
    assert comparison.json()["meta"]["comparison_version"] == "v1"

    discovery = client.post(
        "/api/v1/competitors/discover",
        json={"target_product": product, "search_query": "whey protein powder"},
    )
    assert discovery.status_code == 200
    assert discovery.json()["meta"]["discovery_version"] == "v1"
