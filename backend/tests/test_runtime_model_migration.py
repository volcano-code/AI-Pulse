from pathlib import Path
from types import SimpleNamespace
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select
from app.audited_models import draft_brief_item
from app.bootstrap import bootstrap
from app.db import make_database
from app.demo import seed_demo
from app.models import Snapshot


def test_0008_preserves_existing_snapshots_and_refuses_draft_loss(settings):
    backend=Path(__file__).resolve().parents[1]
    config=Config(str(backend/'alembic.ini'))
    config.set_main_option('script_location',str(backend/'migrations'))
    config.attributes['settings_override']=settings
    command.upgrade(config,'0007')
    engine,factory=make_database(settings)
    try:
        bootstrap(factory,settings);seed_demo(factory,settings)
        with factory() as db:
            before=[(s.id,s.text) for s in db.scalars(select(Snapshot).order_by(Snapshot.id))]
            snap=db.get(Snapshot,before[0][0])
        command.upgrade(config,'0008');command.downgrade(config,'0007');command.upgrade(config,'0008')
        assert 'brief_model_drafts' in inspect(engine).get_table_names()
        class Model:
            def draft(self,title,text):return SimpleNamespace(title=title,summary='Fixture',evidence_quote=text[:40]),{}
        draft_brief_item(factory,settings.model_copy(update={'llm_mode':'live'}),snap.id,snap.title,snap.text,model_client=Model())
        with pytest.raises(RuntimeError,match='validated model drafts'):
            command.downgrade(config,'0007')
        with factory() as db:
            assert [(s.id,s.text) for s in db.scalars(select(Snapshot).order_by(Snapshot.id))]==before
    finally:engine.dispose()


def test_missing_0008_blocks_before_dispatch_and_before_intent(settings):
    from sqlalchemy import func
    from sqlalchemy.exc import OperationalError
    from app.models import ProviderCall
    backend=Path(__file__).resolve().parents[1]
    config=Config(str(backend/'alembic.ini'))
    config.set_main_option('script_location',str(backend/'migrations'))
    config.attributes['settings_override']=settings
    command.upgrade(config,'0007')
    engine,factory=make_database(settings)
    class Model:
        calls=0
        def draft(self,*args):self.calls+=1;raise AssertionError('must not dispatch')
    model=Model()
    try:
        bootstrap(factory,settings);seed_demo(factory,settings)
        with factory() as db:snap=db.scalars(select(Snapshot)).first()
        with pytest.raises(OperationalError):
            draft_brief_item(factory,settings.model_copy(update={'llm_mode':'live'}),snap.id,snap.title,snap.text,model_client=model)
        assert model.calls==0
        with factory() as db:assert db.scalar(select(func.count()).select_from(ProviderCall))==0
    finally:engine.dispose()
