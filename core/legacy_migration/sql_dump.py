"""Read pg_dump's plain-text COPY format without executing any SQL."""

import re

_IDENTIFIER = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
_RELATION = rf"{_IDENTIFIER}(?:\s*\.\s*{_IDENTIFIER})?"
_COPY_HEADER = re.compile(
    rf"^COPY\s+(?P<relation>{_RELATION})\s*"
    r"\((?P<columns>.*?)\)\s+FROM\s+stdin\s*;\s*$",
    re.IGNORECASE,
)
_INSERT_HEADER = re.compile(
    rf"^INSERT\s+INTO\s+(?P<relation>{_RELATION})", re.IGNORECASE
)
_ESCAPE_BYTES = {"b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92}


def _identifier(raw):
    return raw[1:-1].replace('""', '"') if raw.startswith('"') else raw.lower()


def _public_table(relation):
    parts = [_identifier(match.group()) for match in re.finditer(_IDENTIFIER, relation)]
    if len(parts) == 1:
        return parts[0]
    return parts[1] if parts[0] == "public" else None


def _columns(raw):
    columns = []
    remaining = raw.strip()
    while remaining:
        match = re.match(_IDENTIFIER, remaining)
        if match is None:
            raise ValueError("Invalid column list in COPY header.")
        columns.append(_identifier(match.group()))
        remaining = remaining[match.end() :].lstrip()
        if remaining:
            if not remaining.startswith(","):
                raise ValueError("Expected a comma in COPY column list.")
            remaining = remaining[1:].lstrip()
            if not remaining:
                raise ValueError("Trailing comma in COPY column list.")
    if not columns or len(set(columns)) != len(columns):
        raise ValueError("COPY columns must be nonempty and unique.")
    return tuple(columns)


def _decode_value(raw):
    if raw == r"\N":
        return None
    if "\\" not in raw:
        return raw
    # Numeric COPY escapes represent bytes, not Unicode code points. Decode
    # after unescaping so sequences such as \303\251 retain their UTF-8 meaning.
    result = bytearray()
    index = 0
    while index < len(raw):
        char = raw[index]
        index += 1
        if char != "\\":
            result.extend(char.encode("utf-8"))
            continue
        if index == len(raw):
            raise ValueError("Incomplete COPY escape at end of value.")
        escaped = raw[index]
        index += 1
        if escaped in _ESCAPE_BYTES:
            result.append(_ESCAPE_BYTES[escaped])
        elif escaped in "01234567":
            digits = escaped
            while index < len(raw) and len(digits) < 3 and raw[index] in "01234567":
                digits += raw[index]
                index += 1
            value = int(digits, 8)
            if value > 255:
                raise ValueError("COPY octal escape exceeds one byte.")
            result.append(value)
        elif escaped == "x":
            match = re.match(r"[0-9a-fA-F]{1,2}", raw[index:])
            if match is None:
                raise ValueError("Invalid hexadecimal COPY escape.")
            result.append(int(match.group(), 16))
            index += len(match.group())
        else:
            # PostgreSQL treats an otherwise unrecognized escaped character as
            # that character (e.g. an escaped delimiter), without the backslash.
            result.extend(escaped.encode("utf-8"))
    return result.decode("utf-8")


class _SQLContext:
    """Track ignored SQL quoting so function bodies cannot masquerade as COPY."""

    def __init__(self):
        self.statement = False
        self.quote = None
        self.escape_string = False
        self.dollar = None
        self.comment_depth = 0

    @property
    def at_statement_start(self):
        return not (self.statement or self.quote or self.dollar or self.comment_depth)

    def feed(self, line):
        index = 0
        while index < len(line):
            char = line[index]
            pair = line[index : index + 2]
            if self.dollar:
                end = line.find(self.dollar, index)
                if end < 0:
                    return
                index = end + len(self.dollar)
                self.dollar = None
                continue
            if self.quote:
                if self.escape_string and char == "\\":
                    index += 2
                    continue
                if char == self.quote:
                    if pair == self.quote * 2:
                        index += 2
                        continue
                    self.quote = None
                index += 1
                continue
            if pair == "/*":
                self.comment_depth += 1
                index += 2
                continue
            if self.comment_depth:
                if pair == "*/":
                    self.comment_depth -= 1
                    index += 2
                else:
                    index += 1
                continue
            if pair == "--":
                return
            if char.isspace():
                index += 1
                continue
            if char == ";":
                self.statement = False
            else:
                self.statement = True
                if char in {"'", '"'}:
                    self.quote = char
                    self.escape_string = (
                        char == "'"
                        and index > 0
                        and line[index - 1] in "Ee"
                        and (
                            index < 2
                            or not (line[index - 2].isalnum() or line[index - 2] == "_")
                        )
                    )
                elif char == "$":
                    match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", line[index:])
                    if match:
                        self.dollar = match.group()
                        index += len(self.dollar)
                        continue
            index += 1


def iter_postgres_copy_rows(path, tables):
    """Yield (table, columns, row); row=None announces even an empty COPY block.

    Only public/unqualified whitelisted tables are decoded. Other COPY blocks
    are consumed but ignored; DDL, functions, triggers, and commands never run.
    A schema-only dump is rejected rather than treated as a successful import.
    """
    expected = set(tables)
    seen = set()
    in_copy = False
    table = None
    columns = ()
    line_number = 0
    sql_context = _SQLContext()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.removesuffix("\n").removesuffix("\r")
                if in_copy:
                    if line == r"\.":
                        in_copy = False
                        table = None
                        continue
                    if table is not None:
                        values = line.split("\t")
                        if len(values) != len(columns):
                            raise ValueError(
                                f"{table}: expected {len(columns)} COPY values, got {len(values)}."
                            )
                        row = dict(
                            zip(
                                columns,
                                (_decode_value(value) for value in values),
                                strict=True,
                            )
                        )
                        yield table, columns, row
                    continue
                stripped = line.strip()
                if not sql_context.at_statement_start:
                    sql_context.feed(line)
                    continue
                if stripped.startswith("\\"):
                    # psql meta-commands (e.g. newer pg_dump's \\restrict) are
                    # ignored, never passed to a shell or database.
                    continue
                match = _COPY_HEADER.fullmatch(stripped)
                if match:
                    in_copy = True
                    candidate = _public_table(match["relation"])
                    table = candidate if candidate in expected else None
                    if table is not None:
                        if table in seen:
                            raise ValueError(
                                f"Repeated COPY block for {table}; supply a single database dump."
                            )
                        seen.add(table)
                        columns = _columns(match["columns"])
                        yield table, columns, None
                elif re.match(r"COPY\s", stripped, re.IGNORECASE):
                    raise ValueError(
                        "Unsupported COPY syntax; use pg_dump's default plain-text COPY format."
                    )
                else:
                    match = _INSERT_HEADER.match(stripped)
                    if match and _public_table(match["relation"]) in expected:
                        raise ValueError(
                            "INSERT-format dumps are not supported; use pg_dump without --inserts."
                        )
                    sql_context.feed(line)
        if in_copy:
            raise ValueError(
                "Truncated dump: COPY block is missing its \\. terminator."
            )
        missing = expected - seen
        if missing:
            raise ValueError(
                "Missing table data (COPY blocks): "
                + ", ".join(sorted(missing))
                + ". Supply a plain-text pg_dump including data, not a schema-only dump."
            )
    except ValueError as exc:
        raise ValueError(f"PostgreSQL dump near line {line_number}: {exc}") from exc
