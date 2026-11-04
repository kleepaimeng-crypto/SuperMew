"""增加登录凭据；旧无凭据用户必须先显式处理，禁止默认密码。"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    # 在线及离线 SQL 都执行同一前置检查；锁表防止检查之后出现并发旧用户写入。
    op.execute("LOCK TABLE users IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM users) THEN
                RAISE EXCEPTION 'Existing users require an explicit credential migration plan';
            END IF;
        END $$;
    """)
    op.add_column("users", sa.Column("username", sa.String(32), nullable=False))
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=False))
    op.create_unique_constraint("uq_users_username", "users", ["username"])


def downgrade():
    raise RuntimeError("Credential migration is forward-only; restore from a reviewed backup")
