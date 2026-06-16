"""Database persistence layer for Insuretek."""

from core_data.db.dynamodb.db_init import create_tables, delete_tables, list_tables, get_table_status

__all__ = [
    "create_tables",
    "delete_tables",
    "list_tables",
    "get_table_status",
]
