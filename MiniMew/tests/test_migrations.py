from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_initial_migration_has_one_head_and_no_business_ddl():
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    assert ScriptDirectory.from_config(config).get_heads() == ["0014"]
    command.upgrade(config, "0001", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE alembic_version" in sql
    assert sql.count("CREATE TABLE") == 1
    assert "0001" in sql


def test_upgrade_from_initial_version_contains_ownership_constraint():
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "0001:head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE users" in sql
    assert "CREATE TABLE threads" in sql
    assert "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT" in sql
    assert "CREATE INDEX ix_threads_user_id" in sql
