import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.demo import seed_demo

@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'test.db'}", data_mode="replay")

@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c

@pytest.fixture
def seeded(client):
    seed_demo(client.app.state.session_factory, client.app.state.settings)
    return client
