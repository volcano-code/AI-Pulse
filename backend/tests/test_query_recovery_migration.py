from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from app.db import make_database
from app.bootstrap import bootstrap
from app.embedding_gateway import EmbeddingGateway
from app.query_embeddings import embed_saved_query
from test_query_recovery import Spy


def test_0009_preflight_preserves_old_data_and_refuses_cache_loss(settings):
    backend=Path(__file__).resolve().parents[1]
    config=Config(str(backend/'alembic.ini'))
    config.set_main_option('script_location',str(backend/'migrations'))
    config.attributes['settings_override']=settings
    command.upgrade(config,'0008')
    engine,f=make_database(settings)
    s=settings.model_copy(update={'embedding_provider':'fixture','embedding_model':'fixture-sha256-v1','embedding_dim':8})
    p=Spy();g=EmbeddingGateway(p)
    try:
        bootstrap(f,settings)
        with engine.begin() as c:
            c.execute(text('CREATE TABLE preservation_probe (id INTEGER PRIMARY KEY)'))
            c.execute(text('INSERT INTO preservation_probe VALUES (7)'))
        with pytest.raises(OperationalError): embed_saved_query(f,s,'migration probe',g)
        assert p.calls==0
        command.upgrade(config,'0009');command.downgrade(config,'0008');command.upgrade(config,'0009')
        embed_saved_query(f,s,'migration probe',g)
        with pytest.raises(RuntimeError,match='audit history'): command.downgrade(config,'0008')
        with engine.connect() as c:
            assert c.scalar(text('SELECT id FROM preservation_probe'))==7
            assert c.scalar(text('SELECT count(*) FROM query_embeddings'))==1
    finally: engine.dispose()
