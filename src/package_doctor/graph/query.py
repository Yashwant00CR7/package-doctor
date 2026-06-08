"""Query functions for the package relationship graph."""

from typing import Any

import kuzu

from .schema import get_database


def get_package_relationships(db_path: str, package_name: str) -> dict[str, Any]:
    """Get relationships for a package from the graph database.

    Args:
        db_path: Path to the Kuzu database
        package_name: Name of the package to look up

    Returns:
        Dictionary with package name, relationships, and related packages
    """
    db = get_database(db_path)
    conn = kuzu.Connection(db)
    package_lower = package_name.lower()

    # Get package info and relationships
    query = """
    MATCH (p:Package {name: $name})
    OPTIONAL MATCH (p)-[r:HAS_RELATIONSHIP]->(related:Package)
    RETURN p, collect({type: r.relationship_type, related: related.name}) AS relationships
    """

    result = conn.execute(query, {"name": package_lower})

    if not result.has_next():
        return {
            "package": package_name,
            "relationships": {
                "deprecated_by": None,
                "replacement_for": None,
                "fork_of": None,
                "wrapper_for": None,
                "ecosystem": None,
            },
            "related_packages": [],
            "error": "Package not found in graph",
        }

    # Re-execute to get the actual row data
    result = conn.execute(query, {"name": package_lower})
    row = result.get_next()
    package_node = row[0]
    relationships_list = row[1]

    # Extract ecosystem from package node
    ecosystem = package_node.get("ecosystem") if package_node else None

    # Build relationships dict
    relationships = {
        "deprecated_by": None,
        "replacement_for": None,
        "fork_of": None,
        "wrapper_for": None,
        "ecosystem": ecosystem,
    }

    related_packages = []

    for rel in relationships_list:
        rel_type = rel.get("type")
        related_name = rel.get("related")

        if rel_type and related_name:
            if rel_type in relationships:
                relationships[rel_type] = related_name
            if related_name not in related_packages:
                related_packages.append(related_name)

    return {
        "package": package_name,
        "relationships": relationships,
        "related_packages": related_packages,
    }


def get_all_packages(db_path: str, limit: int = 100) -> list[dict[str, str]]:
    """Get all packages in the graph.

    Args:
        db_path: Path to the Kuzu database
        limit: Maximum number of packages to return

    Returns:
        List of package dictionaries with name and ecosystem
    """
    db = get_database(db_path)
    conn = kuzu.Connection(db)

    query = """
    MATCH (p:Package)
    RETURN p.name AS name, p.ecosystem AS ecosystem
    LIMIT $limit
    """

    result = conn.execute(query, {"limit": limit})

    packages = []
    while result.has_next():
        row = result.get_next()
        packages.append({
            "name": row[0],
            "ecosystem": row[1],
        })

    return packages


def find_related_packages(
    db_path: str, package_name: str, relationship_type: str | None = None
) -> list[str]:
    """Find packages related to the given package.

    Args:
        db_path: Path to the Kuzu database
        package_name: Name of the package
        relationship_type: Optional filter for specific relationship type

    Returns:
        List of related package names
    """
    db = get_database(db_path)
    conn = kuzu.Connection(db)
    package_lower = package_name.lower()

    if relationship_type:
        query = """
        MATCH (p:Package {name: $name})-[r:HAS_RELATIONSHIP]->(related:Package)
        WHERE r.type = $rel_type
        RETURN related.name AS name
        """
        result = conn.execute(query, {"name": package_lower, "rel_type": relationship_type})
    else:
        query = """
        MATCH (p:Package {name: $name})-[r:HAS_RELATIONSHIP]->(related:Package)
        RETURN related.name AS name
        """
        result = conn.execute(query, {"name": package_lower})

    related = []
    while result.has_next():
        row = result.get_next()
        related.append(row[0])

    return related
