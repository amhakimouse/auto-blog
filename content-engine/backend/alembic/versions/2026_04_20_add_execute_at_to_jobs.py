"""Add execute_at and QUEUED status to jobs

Revision ID: a1b2c3d4e5f6
Revises: 
Create Date: 2026-04-20 22:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add execute_at column
    op.add_column('jobs', sa.Column('execute_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_jobs_execute_at'), 'jobs', ['execute_at'], unique=False)
    
    # We do not drop or change the status column length, since it was String(20), 
    # which is long enough for "queued". We just rely on the application to handle the enum.


def downgrade() -> None:
    op.drop_index(op.f('ix_jobs_execute_at'), table_name='jobs')
    op.drop_column('jobs', 'execute_at')
