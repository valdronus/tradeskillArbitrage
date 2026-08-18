from __future__ import annotations
"""Shared helper utilities for AuctionAnalysis scripts.

This module centralizes reusable SQLite schema helpers, Lua parsing utilities,
CLI parser construction, debug logging, identifier quoting, and structured
serialization helpers used across AuctionAnalysis tools.
"""

import argparse
import csv
import json
import logging
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, is_dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, TextIO, Tuple

try:
    import slpp
except ImportError:  # pragma: no cover
    slpp = None

LOG_FORMAT = "%(asctime)s %(levelname)s: %(message)s"
ITEM_LINK_RGX = re.compile(r"\|H(item:[^|]+)\|h")
ITEM_NAME_RGX = re.compile(r"\[(?P<name>[^\]]+)\]")

DEFAULT_WOW_ROOT = Path.home() / "World of Warcraft 3.3.5a (no install)" / "WTF"
DEFAULT_ACCOUNT_DIR = DEFAULT_WOW_ROOT / "Account"

SCAN_FIELD_POSITIONS = {
    "link": 1,
    "itemLevel": 2,
    "itemType": 3,
    "subType": 4,
    "equipPos": 5,
    "price": 6,
    "timeLeft": 7,
    "seenTime": 8,
    "itemName": 9,
    "stackSize": 11,
    "quality": 12,
    "canUse": 13,
    "useLevel": 14,
    "minBid": 15,
    "increment": 16,
    "buyoutPrice": 17,
    "curBid": 18,
    "amBidder": 19,
    "sellerName": 20,
    "dataFlag": 21,
    "itemId": 23,
    "itemSuffix": 24,
    "itemFactor": 25,
    "itemEnchant": 26,
    "itemSeed": 27,
}


@dataclass
class ColumnInfo:
    cid: int
    name: str
    type: str
    notnull: int
    dflt_value: Optional[str]
    pk: int


@dataclass
class ColumnSchema:
    name: str
    type: str
    notnull: bool
    default: Optional[str]
    primary_key: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "notnull": self.notnull,
            "default": self.default,
            "primary_key": self.primary_key,
        }


@dataclass
class TableSchema:
    name: str
    columns: List[ColumnSchema]
    ddl: Optional[str] = None

    def column_names(self) -> List[str]:
        return [column.name for column in self.columns]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "columns": [column.as_dict() for column in self.columns],
            "ddl": self.ddl,
        }


@dataclass
class DatabaseSchema:
    tables: Dict[str, TableSchema]

    @property
    def table_names(self) -> List[str]:
        return list(self.tables)

    def as_dict(self) -> Dict[str, Any]:
        return {name: table.as_dict() for name, table in self.tables.items()}


def load_table_schema(
    conn: sqlite3.Connection,
    table: str,
    include_ddl: bool = True,
) -> TableSchema:
    """Load a table's schema into a structured dataclass representation."""
    columns = [
        ColumnSchema(
            name=column.name,
            type=column.type or "",
            notnull=bool(column.notnull),
            default=column.dflt_value,
            primary_key=bool(column.pk),
        )
        for column in get_table_columns(conn, table)
    ]
    ddl = get_table_ddl(conn, table) if include_ddl else None
    return TableSchema(name=table, columns=columns, ddl=ddl)


def load_database_schema(
    conn: sqlite3.Connection,
    include_sqlite_meta: bool = False,
    include_ddl: bool = False,
) -> DatabaseSchema:
    """Load the database schema as a structured dataclass object."""
    tables = get_tables(conn, include_sqlite_meta=include_sqlite_meta)
    return DatabaseSchema(
        tables={
            table: load_table_schema(conn, table, include_ddl=include_ddl)
            for table in tables
        }
    )


def debug_log(level: int, debug_level: int, message: str, stream: TextIO = sys.stderr) -> None:
    """Print a debug message when the configured debug level is high enough.

    Args:
        level (int): The minimum debug level required to print the message.
        debug_level (int): The current debug verbosity level.
        message (str): The message to write to the debug stream.
        stream (TextIO): The output stream to use, typically stderr.

    Returns:
        None: This function only has side effects.
    """
    if debug_level >= level:
        print(f"[debug {level}] {message}", file=stream)


def default_input_path(account: Optional[str] = None) -> Path:
    """Return the default Auctioneer account data input path."""
    account_root = DEFAULT_ACCOUNT_DIR
    if not account_root.exists():
        alt_root = DEFAULT_WOW_ROOT / "Accounts"
        if alt_root.exists():
            account_root = alt_root
    if account and account.strip():
        return account_root / account.strip()
    return account_root / "redacted"


def parse_lua(text: str, debug: int = 0, source: str = "input") -> Any:
    """Parse Auctioneer Lua text using the shared Lua parser.

    Args:
        text (str): Lua source text.
        debug (int): Debug verbosity level, currently unused.
        source (str): Source identifier for error reporting.

    Returns:
        Any: Decoded Lua structure.
    """
    del debug
    return parse_lua_text(text, source=source, allow_return=True)


def make_arg_parser(
    description: str,
    include_debug: bool = True,
    include_verbose: bool = True,
    include_input: bool = False,
    include_output: bool = False,
    include_log: bool = False,
    default_input: Optional[str] = None,
    default_output: Optional[str] = None,
    default_log: Optional[str] = None,
) -> argparse.ArgumentParser:
    """Create a reusable argparse parser with common CLI flags.

    Args:
        description (str): Description text for the parser.
        include_debug (bool): Add a --debug argument if True.
        include_verbose (bool): Add a --verbose argument if True.
        include_input (bool): Add a --input argument if True.
        include_output (bool): Add a --output argument if True.
        include_log (bool): Add a --log argument if True.
        default_input (Optional[str]): Default value for --input.
        default_output (Optional[str]): Default value for --output.
        default_log (Optional[str]): Default value for --log.

    Returns:
        argparse.ArgumentParser: A configured argument parser.
    """
    parser = argparse.ArgumentParser(description=description)

    if include_debug:
        parser.add_argument(
            "--debug",
            type=int,
            default=0,
            help="Increase debug verbosity level (0-3)",
        )

    if include_verbose:
        parser.add_argument(
            "--verbose",
            action="count",
            default=0,
            help="Increase verbosity of console output",
        )

    if include_input:
        parser.add_argument(
            "--input",
            default=default_input,
            help="Input file or directory path",
        )

    if include_output:
        parser.add_argument(
            "--output",
            default=default_output,
            help="Output file path",
        )

    if include_log:
        parser.add_argument(
            "--log",
            default=default_log,
            help="Log file path",
        )

    return parser


def normalize_debug_verbose(args: argparse.Namespace) -> Tuple[int, int]:
    """Normalize debug and verbose CLI arguments into a debug level.

    Args:
        args (argparse.Namespace): Parsed arguments namespace.

    Returns:
        Tuple[int, int]: A tuple containing (debug_level, verbose_count).

    Raises:
        ValueError: If both --debug and --verbose are supplied.
    """
    debug_level = getattr(args, "debug", 0)
    verbose_count = getattr(args, "verbose", 0)
    if debug_level and verbose_count:
        raise ValueError("Use either --debug or --verbose, not both")
    if verbose_count:
        debug_level = min(verbose_count, 3)
    return debug_level, verbose_count


def make_logger(
    name: str,
    log_path: Optional[Path] = None,
    level: int = logging.DEBUG,
    console_level: int = logging.INFO,
    fmt: str = LOG_FORMAT,
) -> logging.Logger:
    """Create and return a configured logger with optional file logging.

    Args:
        name (str): The logger name.
        log_path (Optional[Path]): Optional path for a file log handler.
        level (int): Logging level for the logger and file handler.
        console_level (int): Logging level for console output.
        fmt (str): Log message format string.

    Returns:
        logging.Logger: A configured logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers = []

    formatter = logging.Formatter(fmt)

    if log_path is not None:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def quote_ident(name: str) -> str:
    """Quote and escape an SQLite identifier.

    Args:
        name (str): The identifier name to quote.

    Returns:
        str: The safely quoted identifier.
    """
    return '"' + name.replace('"', '""') + '"'


def get_tables(conn: sqlite3.Connection, include_sqlite_meta: bool = False) -> List[str]:
    """Return table names from an SQLite connection.

    Args:
        conn (sqlite3.Connection): Active SQLite database connection.
        include_sqlite_meta (bool): If True, include sqlite_ internal tables.

    Returns:
        List[str]: Ordered list of table names.
    """
    cursor = conn.cursor()
    where_clause = "" if include_sqlite_meta else "AND name NOT LIKE 'sqlite_%' "
    rows = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        + where_clause
        + "ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def get_table_columns(conn: sqlite3.Connection, table: str) -> List[ColumnInfo]:
    """Return column metadata for a single SQLite table.

    Args:
        conn (sqlite3.Connection): Active SQLite database connection.
        table (str): Table name to inspect.

    Returns:
        List[ColumnInfo]: List of column metadata objects.
    """
    cursor = conn.cursor()
    rows = cursor.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
    return [ColumnInfo(*row) for row in rows]


def get_table_schema(conn: sqlite3.Connection, table: str) -> Dict[str, str]:
    """Return a mapping of column names to declared SQLite types.

    Args:
        conn (sqlite3.Connection): Active SQLite database connection.
        table (str): Table name to inspect.

    Returns:
        Dict[str, str]: Column name to declared type mapping.
    """
    return {column.name: column.type or "" for column in get_table_columns(conn, table)}


def get_table_ddl(conn: sqlite3.Connection, table: str) -> str:
    """Return the original CREATE TABLE statement for a table.

    Args:
        conn (sqlite3.Connection): Active SQLite database connection.
        table (str): Table name to inspect.

    Returns:
        str: The CREATE TABLE DDL statement.

    Raises:
        ValueError: If the table does not exist or its DDL cannot be read.
    """
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"Could not read DDL for table: {table}")
    return row[0]


def format_table_schema(conn: sqlite3.Connection, table: str) -> str:
    """Format a table's schema as SQL text.

    Args:
        conn (sqlite3.Connection): Active SQLite database connection.
        table (str): Table name to format.

    Returns:
        str: A CREATE TABLE-style schema declaration.
    """
    try:
        return get_table_ddl(conn, table)
    except ValueError:
        columns = get_table_columns(conn, table)
        column_defs = []
        for column in columns:
            column_parts = [quote_ident(column.name)]
            if column.type:
                column_parts.append(column.type)
            if column.notnull:
                column_parts.append("NOT NULL")
            if column.dflt_value is not None:
                column_parts.append(f"DEFAULT {column.dflt_value}")
            if column.pk:
                column_parts.append("PRIMARY KEY")
            column_defs.append(" ".join(column_parts))
        return f"CREATE TABLE {quote_ident(table)} ({', '.join(column_defs)})"


def serialize_structured_data(value: Any) -> Any:
    """Convert dataclasses, models, mappings, and sequences into JSON-friendly primitives.

    Args:
        value (Any): Arbitrary Python data, including dataclasses and model objects.

    Returns:
        Any: A serializable Python structure composed of dicts, lists, and primitives.
    """
    if value is None or isinstance(value, (str, bytes, bytearray, bool, int, float)):
        return value

    if is_dataclass(value):
        return serialize_structured_data(asdict(value))

    if isinstance(value, Mapping):
        return {str(key): serialize_structured_data(val) for key, val in value.items()}

    if isinstance(value, (list, tuple, set)):
        serialized = [serialize_structured_data(item) for item in value]
        return tuple(serialized) if isinstance(value, tuple) else serialized

    dict_like = getattr(value, "dict", None)
    if callable(dict_like):
        try:
            model_dict = dict_like()
        except Exception:
            model_dict = None
        if isinstance(model_dict, Mapping):
            return serialize_structured_data(model_dict)

    if hasattr(value, "__dict__"):
        return serialize_structured_data(vars(value))

    return value


def row_to_mapping(row: Any) -> Dict[str, Any]:
    """Convert a database row or row-like object into a simple mapping.

    Args:
        row (Any): A row returned from sqlite3 or any mapping-like object.

    Returns:
        Dict[str, Any]: A simple string-keyed dictionary representation.
    """
    if isinstance(row, Mapping):
        return dict(row)
    if isinstance(row, (list, tuple)):
        return {str(idx): value for idx, value in enumerate(row)}
    if hasattr(row, "__dict__"):
        return serialize_structured_data(vars(row))
    return {"value": row}


def format_money(value: Optional[float], none_text: str = "unknown price") -> str:
    """Format a copper-denominated amount into gold-silver-copper text.

    Args:
        value (Optional[float]): The copper value to format.
        none_text (str): Text to return when value is None.

    Returns:
        str: Formatted currency string.
    """
    if value is None:
        return none_text

    copper = int(round(value))
    gold = copper // 10000
    silver = (copper % 10000) // 100
    copper_remainder = copper % 100
    parts = []
    if gold:
        parts.append(f"{gold}g")
    if silver or gold:
        parts.append(f"{silver}s")
    parts.append(f"{copper_remainder}c")
    return " ".join(parts)


def parse_item_link(
    value: str,
    link_regex: re.Pattern[str] = ITEM_LINK_RGX,
    name_regex: re.Pattern[str] = ITEM_NAME_RGX,
) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """Parse a WoW item link string into its link, item ID, and item name.

    Args:
        value (str): The raw value containing an item link.
        link_regex (re.Pattern[str]): Regular expression to extract the item link.
        name_regex (re.Pattern[str]): Regular expression to extract the item name.

    Returns:
        Tuple[Optional[str], Optional[int], Optional[str]]: A tuple of (link, item_id, item_name).
    """
    value = value or ""
    link = None
    item_id = None
    item_name = None

    match = link_regex.search(value)
    if match:
        link = match.groupdict().get("link") or match.group(1)
    elif value.startswith("item:"):
        link = value

    if link:
        item_name_match = name_regex.search(value)
        if item_name_match:
            item_name = item_name_match.groupdict().get("name") or item_name_match.group(1)

        tokens = link.split(":")
        if len(tokens) > 1:
            try:
                item_id = int(tokens[1])
            except ValueError:
                pass

    return link, item_id, item_name


def write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fieldnames: Optional[Sequence[str]] = None,
    encoding: str = "utf-8",
) -> None:
    """Write a sequence of row mappings to a CSV file.

    Args:
        path (Path): Output CSV file path.
        rows (Sequence[Mapping[str, Any]]): Row data to write.
        fieldnames (Optional[Sequence[str]]): Optional ordered field names.
        encoding (str): File encoding to use.

    Returns:
        None
    """
    if fieldnames is None:
        fieldnames = sorted({field for row in rows for field in row})

    with path.open("w", newline="", encoding=encoding) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(
    path: Path,
    data: Any,
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
    encoding: str = "utf-8",
) -> None:
    """Write Python data as JSON to a file.

    Args:
        path (Path): Output JSON file path.
        data (Any): Data structure to serialize.
        indent (int): Indentation level for JSON formatting.
        sort_keys (bool): Whether to sort dictionary keys.
        ensure_ascii (bool): Whether to escape non-ASCII characters.
        encoding (str): File encoding to use.

    Returns:
        None
    """
    path.write_text(
        json.dumps(data, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii)
        + "\n",
        encoding=encoding,
    )


def write_json_output(
    path: Path,
    data: Any,
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
    encoding: str = "utf-8",
) -> None:
    """Write structured data to JSON output and preserve the standard encoding.

    Args:
        path (Path): Output file path.
        data (Any): Data to serialize.
        indent (int): Indentation level.
        sort_keys (bool): Whether JSON output should sort object keys.
        ensure_ascii (bool): Whether to escape non-ASCII text.
        encoding (str): File encoding.

    Returns:
        None
    """
    write_json(
        path,
        data,
        indent=indent,
        sort_keys=sort_keys,
        ensure_ascii=ensure_ascii,
        encoding=encoding,
    )


def parse_lua_text(
    text: str,
    logger: Optional[logging.Logger] = None,
    source: str = "input",
    allow_return: bool = True,
) -> Any:
    if slpp is None:
        raise ImportError("slpp is required to parse Lua text")

    normalized = text.strip()
    if not normalized:
        raise ValueError(f"Lua text from {source} is empty")

    if allow_return and normalized.startswith("return"):
        normalized = normalized[len("return") :].strip()

    try:
        parsed = slpp.decode(normalized)
    except Exception as exc:
        if logger is not None:
            logger.exception("Failed to parse Lua source %s", source)
        raise RuntimeError(f"Could not parse Lua source '{source}': {exc}") from exc

    if isinstance(parsed, str) and "=" in normalized:
        assignment_match = re.search(r"^[^=]+=[ \t\r\n]*(\{.+)$", normalized, re.DOTALL)
        if assignment_match:
            parsed = slpp.decode(assignment_match.group(1))

    return parsed


def parse_lua_file(
    path: Path,
    logger: Optional[logging.Logger] = None,
    source: Optional[str] = None,
    allow_return: bool = True,
) -> Any:
    text = path.read_text(encoding="utf-8", errors="ignore")
    source_name = source or str(path)
    if logger is not None:
        logger.debug("Parsing Lua file: %s", source_name)
    return parse_lua_text(text, logger=logger, source=source_name, allow_return=allow_return)
