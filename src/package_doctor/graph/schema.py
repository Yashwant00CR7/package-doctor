"""Kuzu graph schema initialization for PyPI package relationships."""

import kuzu


def get_schema_statements() -> list[str]:
    """Returns list of Kuzu schema statements."""
    return [
        """
        CREATE NODE TABLE IF NOT EXISTS Package (
            name STRING PRIMARY KEY,
            ecosystem STRING
        )
        """,
        """
        CREATE REL TABLE IF NOT EXISTS DEPRECATED_BY (
            FROM Package TO Package,
            relationship_type STRING
        )
        """,
        """
        CREATE REL TABLE IF NOT EXISTS REPLACEMENT_FOR (
            FROM Package TO Package,
            relationship_type STRING
        )
        """,
        """
        CREATE REL TABLE IF NOT EXISTS FORK_OF (
            FROM Package TO Package,
            relationship_type STRING
        )
        """,
        """
        CREATE REL TABLE IF NOT EXISTS WRAPPER_FOR (
            FROM Package TO Package,
            relationship_type STRING
        )
        """,
        """
        CREATE REL TABLE IF NOT EXISTS HAS_RELATIONSHIP (
            FROM Package TO Package,
            relationship_type STRING
        )
        """
    ]


def init_database(db_path: str) -> kuzu.Database:
    """Initialize the Kuzu database with the schema.

    Args:
        db_path: Path to the database file

    Returns:
        Configured Database instance
    """
    db = kuzu.Database(db_path)
    conn = kuzu.Connection(db)
    for stmt in get_schema_statements():
        conn.execute(stmt)
    return db


def get_database(db_path: str) -> kuzu.Database:
    """Open an existing Kuzu database.

    Args:
        db_path: Path to the database file

    Returns:
        Database instance
    """
    return kuzu.Database(db_path)
