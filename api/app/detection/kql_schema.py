"""Check model-written Defender XDR KQL against the Advanced Hunting table and column catalog.

The catalog ships with the Kusto backend (`sigma.pipelines.microsoftxdr.tables`), so it moves with the pinned
converter. This is a name check, not a KQL parser: it finds the tables a query reads and every column-like name
it uses, and reports tables or columns Defender doesn't have. Defender column names are PascalCase, while KQL
keywords, operators and functions are lowercase, which is what makes a lexical check reliable enough to catch
invented columns before review. A syntax check against real Defender happens only at enterprise handoff.

The catalog is a snapshot (its file header gives the date), cleaned and topped up below for the seven endpoint
event tables. A column Defender added after that is reported as unknown, so a reviewer who knows the column exists
can accept the query as written.
"""

import re
from dataclasses import dataclass, field

from sigma.pipelines.microsoftxdr.tables import MICROSOFT_XDR_TABLES

# Columns the snapshot lacks, from the Microsoft Learn schema pages (checked 2026-11-22). The generator that built the
# snapshot read a docs repository that no longer exists, so upstream won't refresh it. Names only, no descriptions.
_ADDED_SINCE_SNAPSHOT: dict[str, tuple[str, ...]] = {
    "DeviceProcessEvents": ("ProcessUniqueId", "InitiatingProcessUniqueId", "LogonID"),
    "DeviceNetworkEvents": ("InitiatingProcessUniqueId", "LogonID"),
    "DeviceFileEvents": ("InitiatingProcessUniqueId", "LogonID"),
    "DeviceRegistryEvents": ("InitiatingProcessUniqueId",),
    "DeviceEvents": ("InitiatingProcessUniqueId",),
    "DeviceImageLoadEvents": ("InitiatingProcessUniqueId",),
    "DeviceLogonEvents": ("InitiatingProcessUniqueId",),
}


def _catalog() -> dict[str, list[str]]:
    """The snapshot's column names in its order, cleaned of the generator's parse artifacts (a leading space on
    DeviceEvents' InitiatingProcessVersionInfoInternalFileName, stray backticks and asterisks in EmailEvents)."""
    catalog: dict[str, list[str]] = {}
    for name, columns in MICROSOFT_XDR_TABLES.items():
        cleaned = (column.strip().strip("`*").strip() for column in columns)
        catalog[name] = list(dict.fromkeys([*filter(None, cleaned), *_ADDED_SINCE_SNAPSHOT.get(name, ())]))
    return catalog


CATALOG = _catalog()
TABLES: dict[str, set[str]] = {name: set(columns) for name, columns in CATALOG.items()}

# String literals and line comments carry no names. One pass, so "//" inside a string isn't a comment. Verbatim
# strings (@"C:\Temp\") treat a backslash literally and double the quote to escape it; plain strings use \.
_STRINGS_AND_COMMENTS = re.compile(
    r"""[hH]?@"(?:[^"\n]|"")*"|[hH]?@'(?:[^'\n]|'')*'"""
    r"""|[hH]?"(?:[^"\\\n]|\\.)*"|[hH]?'(?:[^'\\\n]|\\.)*'"""
    r"""|//[^\n]*"""
)
# Not inside another token: skips the T00 and Z in datetime(2024-05-01T00:00:00Z) and 1h-style timespans.
_IDENT = re.compile(r"(?<![\w.$])[A-Za-z_][A-Za-z0-9_]*")
_LET = re.compile(r"^\s*let\s+([A-Za-z_]\w*)\s*=\s*", re.IGNORECASE)
_ASSIGNED = re.compile(r"\b([A-Za-z_]\w*)\s*=(?![=~])")  # extend X = .., project X = .., summarize X = ..
_ALIASED = re.compile(r"\bas\s+([A-Za-z_]\w*)")
_OPTIONS = r"(?:\s+[a-z][a-z.]*\s*=\s*\w+)*"  # kind=inner, hint.strategy=shuffle, withsource=T
_JOINED = re.compile(r"\b(?:join|lookup)\b" + _OPTIONS + r"\s*\(?\s*([A-Za-z_]\w*)")
_UNION = re.compile(r"\bunion\b" + _OPTIONS + r"([^|;]*)")
_WITHSOURCE = re.compile(r"\bwithsource\s*=\s*([A-Za-z_]\w*)")
_PARSED = re.compile(r"\bparse(?:-where|-kv)?\b[^|;]*?\bwith\b([^|;]*)")  # parse X with * "a" Name " " *
_DATATABLE = re.compile(r"\bdatatable\s*\(([^)]*)\)")  # datatable(Name:string, ...)


@dataclass
class SchemaCheck:
    tables: list[str] = field(default_factory=list)
    unknown_tables: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        errors = []
        if not self.tables and not self.unknown_tables:
            errors.append("The query reads no Defender XDR table.")
        if self.unknown_tables:
            errors.append(f"Unknown Defender XDR tables: {', '.join(self.unknown_tables)}.")
        if self.unknown_columns:
            read = ", ".join(self.tables) or "the tables read"
            errors.append(f"Columns not in {read}: {', '.join(self.unknown_columns)}.")
        return errors


def _strip(kql: str) -> str:
    return _STRINGS_AND_COMMENTS.sub(lambda match: " " if match.group(0).startswith("//") else '""', kql)


def _table_references(code: str, lets: set[str]) -> list[str]:
    names: list[str] = []
    for statement in code.split(";"):
        body = _LET.sub("", statement, count=1).lstrip("( \t\r\n")
        first = _IDENT.match(body)
        if first:
            names.append(first.group(0))
    names += [match.group(1) for match in _JOINED.finditer(code)]
    for match in _UNION.finditer(code):
        names += [ident for part in match.group(1).split(",") if (ident := _first_ident(part))]
    # Any catalog table name read as a table (not a member or a function call), wherever it sits: inside
    # materialize(...) or toscalar(...), or in a later union subquery.
    for match in _IDENT.finditer(code):
        if match.group(0) in TABLES and not code[match.end() :].lstrip().startswith(("(", "=")):
            names.append(match.group(0))
    # Lowercase names are keywords or functions (`print`, `datatable`, `range`), and let-bound names are local.
    return [name for name in dict.fromkeys(names) if name[0].isupper() and name not in lets]


def _first_ident(text: str) -> str | None:
    match = _IDENT.search(text)
    return match.group(0) if match else None


def check_kql(kql: str) -> SchemaCheck:
    code = _strip(kql)
    lets = {match.group(1) for statement in code.split(";") if (match := _LET.match(statement))}
    references = _table_references(code, lets)
    tables = [name for name in references if name in TABLES]
    unknown_tables = [name for name in references if name not in TABLES]
    columns = set().union(*(TABLES[name] for name in tables)) if tables else set()
    defined = (
        lets
        | {match.group(1) for match in _ASSIGNED.finditer(code)}
        | {match.group(1) for match in _ALIASED.finditer(code)}
        | {match.group(1) for match in _WITHSOURCE.finditer(code)}
        | {ident.group(0) for match in _PARSED.finditer(code) for ident in _IDENT.finditer(match.group(1))}
        | {ident.group(1) for match in _DATATABLE.finditer(code) for ident in re.finditer(r"([A-Za-z_]\w*)\s*:", match.group(1))}
    )
    unknown: dict[str, None] = {}
    for match in _IDENT.finditer(code):
        name = match.group(0)
        before = code[: match.start()].rstrip()
        after = code[match.end() :].lstrip()
        if not name[0].isupper() or name in TABLES or name in unknown_tables or name in defined or name in columns:
            continue
        if before.endswith(".") or after.startswith("("):  # a dynamic member (AdditionalFields.X) or a function
            continue
        unknown[name] = None
    return SchemaCheck(tables=tables, unknown_tables=unknown_tables, unknown_columns=list(unknown))


def catalog_for_prompt(tables: tuple[str, ...]) -> str:
    """Column names of the given tables, one table per line, for the KQL-writing prompt."""
    return "\n".join(f"{name}: {', '.join(CATALOG[name])}" for name in tables)
