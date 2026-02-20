"""Connecteurs bases de données cloud : Snowflake et Databricks.

Gère l'import (lecture depuis la base) et l'export (écriture vers la base)
pour Snowflake et Databricks SQL.

Dépendances optionnelles :
- snowflake-connector-python : pip install snowflake-connector-python
- databricks-sql-connector   : pip install databricks-sql-connector
"""

import csv
import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Pattern strict pour les identifiants SQL — alphanumérique + underscore uniquement
_SAFE_IDENTIFIER_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _validate_identifier(name: str, label: str = 'identifier') -> str:
    """Valide et nettoie un identifiant SQL pour éviter les injections.

    Args:
        name: Nom à valider.
        label: Description pour les messages d'erreur.

    Returns:
        Identifiant nettoyé (double-quoted pour sécurité).

    Raises:
        ValueError: Si le nom contient des caractères invalides.
    """
    from core.exporter import _sanitize_sql_name
    clean = _sanitize_sql_name(name)
    if not clean:
        raise ValueError(f"Identifiant SQL invalide pour {label}: '{name}'")
    return clean


def _validate_query_readonly(query: str) -> str:
    """Valide qu'une requête SQL est en lecture seule (SELECT).

    Args:
        query: Requête SQL à valider.

    Returns:
        La requête validée.

    Raises:
        ValueError: Si la requête contient des instructions DDL/DML.
    """
    stripped = query.strip().rstrip(';').strip()
    # Vérifie que ça commence par SELECT ou WITH (CTE)
    upper = stripped.upper()
    if not (upper.startswith('SELECT') or upper.startswith('WITH')):
        raise ValueError(
            "Seules les requêtes SELECT sont autorisées pour l'import. "
            f"Requête reçue : {stripped[:50]}..."
        )
    # Vérifie l'absence de DDL/DML dangereux en dehors d'une sous-requête
    dangerous = ['DROP ', 'DELETE ', 'UPDATE ', 'INSERT ', 'ALTER ',
                 'TRUNCATE ', 'CREATE ', 'GRANT ', 'REVOKE ', 'EXEC ']
    for keyword in dangerous:
        if keyword in upper:
            raise ValueError(
                f"Requête potentiellement dangereuse détectée (contient {keyword.strip()}). "
                "Seules les requêtes SELECT sont autorisées pour l'import."
            )
    return query


def _build_qualified_name(*parts: Optional[str]) -> str:
    """Construit un nom qualifié (database.schema.table) avec des identifiants validés.

    Args:
        *parts: Parties du nom qualifié (None sont ignorées).

    Returns:
        Nom qualifié avec identifiants nettoyés.
    """
    clean_parts = []
    for part in parts:
        if part:
            clean_parts.append(_validate_identifier(part))
    return '.'.join(clean_parts)


# ═══════════════════════════════════════════════════════════════════════
# SNOWFLAKE
# ═══════════════════════════════════════════════════════════════════════

def snowflake_connect(
    account: str,
    user: str,
    password: str,
    warehouse: Optional[str] = None,
    database: Optional[str] = None,
    schema: str = 'PUBLIC',
    role: Optional[str] = None,
):
    """Crée une connexion Snowflake.

    Args:
        account: Identifiant du compte Snowflake (ex: xy12345.eu-west-1).
        user: Nom d'utilisateur.
        password: Mot de passe.
        warehouse: Entrepôt de calcul.
        database: Base de données.
        schema: Schéma (défaut: PUBLIC).
        role: Rôle Snowflake.

    Returns:
        Connexion Snowflake.
    """
    try:
        import snowflake.connector
    except ImportError:
        raise ImportError(
            "snowflake-connector-python est requis. "
            "Installez-le avec : pip install snowflake-connector-python"
        )

    params = {
        'account': account,
        'user': user,
        'password': password,
        'schema': schema,
    }
    if warehouse:
        params['warehouse'] = warehouse
    if database:
        params['database'] = database
    if role:
        params['role'] = role

    conn = snowflake.connector.connect(**params)
    logger.info(f"Connexion Snowflake établie : {account}")
    return conn


def snowflake_import(
    account: str,
    user: str,
    password: str,
    query: str,
    warehouse: Optional[str] = None,
    database: Optional[str] = None,
    schema: str = 'PUBLIC',
    role: Optional[str] = None,
) -> tuple[list[str], list[list[str]], int]:
    """Importe des données depuis Snowflake via une requête SQL.

    Seules les requêtes SELECT sont autorisées.

    Args:
        account: Identifiant du compte.
        user: Utilisateur.
        password: Mot de passe.
        query: Requête SQL (SELECT ...).
        warehouse: Entrepôt.
        database: Base.
        schema: Schéma.
        role: Rôle.

    Returns:
        Tuple (header, rows, total_lines).
    """
    query = _validate_query_readonly(query)
    conn = snowflake_connect(account, user, password, warehouse, database, schema, role)
    cursor = conn.cursor()

    try:
        cursor.execute(query)
        header = [desc[0] for desc in cursor.description]

        rows = []
        for row in cursor:
            rows.append([str(v) if v is not None else '' for v in row])

        total_lines = len(rows) + 1
        logger.info(f"Snowflake import : {total_lines} lignes, {len(header)} colonnes")
        return header, rows, total_lines

    finally:
        cursor.close()
        conn.close()


def snowflake_import_to_csv(
    account: str,
    user: str,
    password: str,
    query: str,
    output_path: Optional[Path] = None,
    separator: str = ';',
    **kwargs,
) -> Path:
    """Importe depuis Snowflake et sauvegarde en CSV temporaire.

    Returns:
        Chemin du fichier CSV.
    """
    import os

    header, rows, _ = snowflake_import(account, user, password, query, **kwargs)

    if output_path is None:
        fd, tmp_path = tempfile.mkstemp(suffix='.csv', prefix='csvtraitement_sf_')
        os.close(fd)
        output_path = Path(tmp_path)

    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=separator, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)

    logger.info(f"Snowflake → CSV : {output_path.name} ({len(rows)} lignes)")
    return output_path


def snowflake_export(
    header: list[str],
    rows: list[list[str]],
    account: str,
    user: str,
    password: str,
    table_name: str,
    warehouse: Optional[str] = None,
    database: Optional[str] = None,
    schema: str = 'PUBLIC',
    role: Optional[str] = None,
    drop_existing: bool = True,
    col_types: Optional[list[str]] = None,
    progress_callback=None,
) -> str:
    """Exporte des données vers Snowflake.

    Args:
        header: En-têtes de colonnes.
        rows: Lignes de données.
        account: Identifiant du compte.
        user: Utilisateur.
        password: Mot de passe.
        table_name: Nom de la table cible.
        warehouse: Entrepôt.
        database: Base.
        schema: Schéma.
        role: Rôle.
        drop_existing: Supprimer la table si elle existe.
        col_types: Types SQL par colonne (déduits si None).
        progress_callback: Fonction de suivi.

    Returns:
        Nom complet de la table (database.schema.table).
    """
    # Valider tous les identifiants
    safe_table = _validate_identifier(table_name, 'table_name')
    col_names = [_validate_identifier(h, f'column_{i}') for i, h in enumerate(header)]

    if col_types is None:
        col_types = ['VARCHAR' for _ in col_names]

    # Valider les types SQL (seulement des types connus)
    _allowed_types = {'VARCHAR', 'STRING', 'TEXT', 'INTEGER', 'INT', 'NUMERIC',
                      'FLOAT', 'DOUBLE', 'BOOLEAN', 'DATE', 'TIMESTAMP',
                      'VARCHAR(255)', 'REAL', 'BIGINT', 'SMALLINT'}
    col_types = [t if t.upper() in _allowed_types else 'VARCHAR' for t in col_types]

    if database:
        full_table = _build_qualified_name(database, schema, safe_table)
    else:
        full_table = safe_table

    conn = snowflake_connect(account, user, password, warehouse, database, schema, role)
    cursor = conn.cursor()

    try:
        if drop_existing:
            cursor.execute(f"DROP TABLE IF EXISTS {full_table}")

        col_defs = ', '.join(f'{n} {t}' for n, t in zip(col_names, col_types))
        cursor.execute(f"CREATE TABLE {full_table} ({col_defs})")

        if progress_callback:
            progress_callback(0, len(rows), "Insertion Snowflake...")

        # INSERT par batch avec requêtes paramétrées
        placeholders = ', '.join(['%s'] * len(col_names))
        insert_sql = f"INSERT INTO {full_table} ({', '.join(col_names)}) VALUES ({placeholders})"

        batch_size = 5000
        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start:batch_start + batch_size]
            normalized = []
            for row in batch:
                r = list(row[:len(col_names)])
                while len(r) < len(col_names):
                    r.append(None)
                r = [v if v and v.strip() else None for v in r]
                normalized.append(r)
            cursor.executemany(insert_sql, normalized)

            if progress_callback:
                done = min(batch_start + batch_size, len(rows))
                progress_callback(done, len(rows), f"Snowflake: {done}/{len(rows)}")

        conn.commit()
        logger.info(f"Export Snowflake terminé: {full_table} ({len(rows)} lignes)")

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return full_table


# ═══════════════════════════════════════════════════════════════════════
# DATABRICKS
# ═══════════════════════════════════════════════════════════════════════

def databricks_connect(
    server_hostname: str,
    http_path: str,
    access_token: str,
    catalog: Optional[str] = None,
    schema: str = 'default',
):
    """Crée une connexion Databricks SQL.

    Args:
        server_hostname: Hostname du workspace (ex: adb-xxx.azuredatabricks.net).
        http_path: Chemin HTTP du SQL warehouse (ex: /sql/1.0/warehouses/xxx).
        access_token: Token d'accès personnel.
        catalog: Catalogue Unity (optionnel).
        schema: Schéma (défaut: default).

    Returns:
        Connexion Databricks.
    """
    try:
        from databricks import sql as dbsql
    except ImportError:
        raise ImportError(
            "databricks-sql-connector est requis. "
            "Installez-le avec : pip install databricks-sql-connector"
        )

    conn = dbsql.connect(
        server_hostname=server_hostname,
        http_path=http_path,
        access_token=access_token,
    )

    cursor = conn.cursor()
    if catalog:
        safe_catalog = _validate_identifier(catalog, 'catalog')
        cursor.execute(f"USE CATALOG {safe_catalog}")
    safe_schema = _validate_identifier(schema, 'schema')
    cursor.execute(f"USE SCHEMA {safe_schema}")
    cursor.close()

    logger.info(f"Connexion Databricks établie : {server_hostname}")
    return conn


def databricks_import(
    server_hostname: str,
    http_path: str,
    access_token: str,
    query: str,
    catalog: Optional[str] = None,
    schema: str = 'default',
) -> tuple[list[str], list[list[str]], int]:
    """Importe des données depuis Databricks via une requête SQL.

    Seules les requêtes SELECT sont autorisées.

    Args:
        server_hostname: Hostname du workspace.
        http_path: Chemin HTTP du warehouse.
        access_token: Token d'accès.
        query: Requête SQL.
        catalog: Catalogue Unity.
        schema: Schéma.

    Returns:
        Tuple (header, rows, total_lines).
    """
    query = _validate_query_readonly(query)
    conn = databricks_connect(server_hostname, http_path, access_token, catalog, schema)
    cursor = conn.cursor()

    try:
        cursor.execute(query)
        header = [desc[0] for desc in cursor.description]

        rows = []
        for row in cursor:
            rows.append([str(v) if v is not None else '' for v in row])

        total_lines = len(rows) + 1
        logger.info(f"Databricks import : {total_lines} lignes, {len(header)} colonnes")
        return header, rows, total_lines

    finally:
        cursor.close()
        conn.close()


def databricks_import_to_csv(
    server_hostname: str,
    http_path: str,
    access_token: str,
    query: str,
    output_path: Optional[Path] = None,
    separator: str = ';',
    **kwargs,
) -> Path:
    """Importe depuis Databricks et sauvegarde en CSV temporaire.

    Returns:
        Chemin du fichier CSV.
    """
    import os

    header, rows, _ = databricks_import(
        server_hostname, http_path, access_token, query, **kwargs
    )

    if output_path is None:
        fd, tmp_path = tempfile.mkstemp(suffix='.csv', prefix='csvtraitement_db_')
        os.close(fd)
        output_path = Path(tmp_path)

    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=separator, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)

    logger.info(f"Databricks → CSV : {output_path.name} ({len(rows)} lignes)")
    return output_path


def databricks_export(
    header: list[str],
    rows: list[list[str]],
    server_hostname: str,
    http_path: str,
    access_token: str,
    table_name: str,
    catalog: Optional[str] = None,
    schema: str = 'default',
    drop_existing: bool = True,
    col_types: Optional[list[str]] = None,
    progress_callback=None,
) -> str:
    """Exporte des données vers Databricks.

    Args:
        header: En-têtes de colonnes.
        rows: Lignes de données.
        server_hostname: Hostname du workspace.
        http_path: Chemin HTTP du warehouse.
        access_token: Token d'accès.
        table_name: Nom de la table.
        catalog: Catalogue Unity.
        schema: Schéma.
        drop_existing: Supprimer la table existante.
        col_types: Types SQL par colonne.
        progress_callback: Fonction de suivi.

    Returns:
        Nom complet de la table.
    """
    # Valider tous les identifiants
    safe_table = _validate_identifier(table_name, 'table_name')
    col_names = [_validate_identifier(h, f'column_{i}') for i, h in enumerate(header)]

    if col_types is None:
        col_types = ['STRING' for _ in col_names]

    # Valider les types SQL
    _allowed_types = {'VARCHAR', 'STRING', 'TEXT', 'INTEGER', 'INT', 'NUMERIC',
                      'FLOAT', 'DOUBLE', 'BOOLEAN', 'DATE', 'TIMESTAMP',
                      'VARCHAR(255)', 'REAL', 'BIGINT', 'SMALLINT'}
    col_types = [t if t.upper() in _allowed_types else 'STRING' for t in col_types]

    full_table = _build_qualified_name(catalog, schema, safe_table)

    conn = databricks_connect(server_hostname, http_path, access_token, catalog, schema)
    cursor = conn.cursor()

    try:
        if drop_existing:
            cursor.execute(f"DROP TABLE IF EXISTS {full_table}")

        col_defs = ', '.join(f'{n} {t}' for n, t in zip(col_names, col_types))
        cursor.execute(f"CREATE TABLE {full_table} ({col_defs})")

        if progress_callback:
            progress_callback(0, len(rows), "Insertion Databricks...")

        # INSERT par batch avec requêtes paramétrées (sécurisé)
        placeholders = ', '.join(['%s'] * len(col_names))
        insert_sql = f"INSERT INTO {full_table} ({', '.join(col_names)}) VALUES ({placeholders})"

        batch_size = 1000
        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start:batch_start + batch_size]
            for row in batch:
                r = list(row[:len(col_names)])
                while len(r) < len(col_names):
                    r.append(None)
                r = [v if v and v.strip() else None for v in r]
                cursor.execute(insert_sql, r)

            if progress_callback:
                done = min(batch_start + batch_size, len(rows))
                progress_callback(done, len(rows), f"Databricks: {done}/{len(rows)}")

        logger.info(f"Export Databricks terminé: {full_table} ({len(rows)} lignes)")

    finally:
        cursor.close()
        conn.close()

    return full_table
