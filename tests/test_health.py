import pytest
from django.test import Client

from ledger.dbidentity import database_identity


@pytest.mark.django_db
def test_health_checks_database_connection(client: Client) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "database_id": database_identity(),
    }


def test_swagger_docs_are_served(client: Client) -> None:
    assert client.get("/api/v1/docs").status_code == 200
