#!/usr/bin/env python
"""
Rebuild nested JSON objects from a denormalized CSV, such as the result of a
join. Rows are merged by business identity: a schema declares the parent-child
paths and the (composite) key of each level, and every input row is folded into
the objects it identifies, instead of producing one flat object per row.

Used by ``csvjson --rebuild``. See docs/scripts/csvjson.rst for the schema
format and the merge semantics.
"""

import datetime
import decimal
import json
import os
import sqlite3
import tempfile
from collections import OrderedDict

from csvkit.cli import default_float_decimal, match_column_identifier
from csvkit.exceptions import ColumnIdentifierError

SCHEMA_OPTIONS = ('key', 'columns', 'order_by', 'children')

#: How many conflicts are reported in detail before only the total is summarized.
MAX_REPORTED_CONFLICTS = 1000

#: Diagnostics are buffered and flushed to the error file in batches of this
#: many lines, so that a conflict storm does not become a write() storm.
_FLUSH_EVERY = 100


class RebuildSchemaError(ValueError):
    """
    Exception raised when a rebuild schema is invalid.
    """


def load_schema(path):
    """
    Read a rebuild schema JSON file and return the root :class:`Level`.
    """
    try:
        with open(path, encoding='utf-8-sig') as f:
            spec = json.load(f, object_pairs_hook=OrderedDict)
    except OSError as e:
        raise RebuildSchemaError(f'Unable to read the rebuild schema "{path}": {e.strerror or e}')
    except json.JSONDecodeError as e:
        raise RebuildSchemaError(f'The rebuild schema "{path}" is not valid JSON: {e}')

    return Level('', spec, ())


def _as_string_list(value, option, level_name):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise RebuildSchemaError(
        f'The rebuild schema option "{option}" of level "{level_name or "(root)"}" must be a string '
        'or a list of strings.'
    )


class Level:
    """
    One object level of a rebuild schema: the root or one child array.
    """

    def __init__(self, name, spec, path):
        self.name = name  # the array name in the parent object; '' for the root
        self.path = path  # the tuple of array names from the root to this level
        self.children = OrderedDict()  # array name -> Level
        self.column_map = OrderedDict()  # CSV column identifier -> output field name
        self.fields = OrderedDict()  # output field name -> CSV column index (set by resolve_columns)

        where = f'level "{name}"' if name else 'the root level'
        if not isinstance(spec, dict):
            raise RebuildSchemaError(f'The rebuild schema for {where} must be a JSON object.')

        unknown = sorted(set(spec) - set(SCHEMA_OPTIONS))
        if unknown:
            raise RebuildSchemaError(
                f'The rebuild schema for {where} has unknown option(s): {", ".join(unknown)}. '
                f'Valid options are: {", ".join(SCHEMA_OPTIONS)}.'
            )

        if 'key' not in spec:
            raise RebuildSchemaError(f'The rebuild schema for {where} must declare a "key".')
        self.key = _as_string_list(spec['key'], 'key', name)
        if not self.key:
            raise RebuildSchemaError(f'The rebuild schema for {where} must not declare an empty "key".')

        self.order_by = _as_string_list(spec.get('order_by', []), 'order_by', name)

        columns = spec.get('columns', {})
        if not isinstance(columns, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v for k, v in columns.items()
        ):
            raise RebuildSchemaError(
                f'The rebuild schema option "columns" of {where} must be an object mapping '
                'CSV column names to output field names.'
            )
        self.column_map = columns

        children = spec.get('children', {})
        if not isinstance(children, dict):
            raise RebuildSchemaError(f'The rebuild schema option "children" of {where} must be an object.')
        for child_name, child_spec in children.items():
            if not isinstance(child_name, str) or not child_name or '.' in child_name:
                raise RebuildSchemaError(
                    f'The rebuild schema for {where} has an invalid child name: {child_name!r}. '
                    'Child names must be non-empty and must not contain dots.'
                )
            self.children[child_name] = Level(child_name, child_spec, path + (child_name,))

    def walk(self):
        yield self
        for child in self.children.values():
            yield from child.walk()

    def dotted_path(self):
        return '.'.join(self.path)

    def resolve_columns(self, column_names, column_offset=1):
        """
        Assign every CSV column to a level and validate the key and order_by
        fields against the resolved columns.
        """
        claimed = {}

        # 1. Columns explicitly mapped in the schema.
        for level in self.walk():
            for identifier, field in level.column_map.items():
                try:
                    index = match_column_identifier(column_names, identifier, column_offset)
                except ColumnIdentifierError as e:
                    raise RebuildSchemaError(f'The rebuild schema is invalid: {e}')
                if index in claimed:
                    raise RebuildSchemaError(
                        f'The rebuild schema maps the column "{column_names[index]}" more than once.'
                    )
                claimed[index] = level
                level.add_field(field, index)

        # 2. Unmapped columns are assigned by dotted-path prefix
        #    ("items.discounts.code"), the deepest matching level first.
        levels = sorted(self.walk(), key=lambda level: len(level.path), reverse=True)
        for index, column_name in enumerate(column_names):
            if index in claimed:
                continue
            parts = column_name.split('.')
            for level in levels:
                if not level.path:
                    continue
                if len(parts) > len(level.path) and tuple(parts[:len(level.path)]) == level.path:
                    level.add_field('.'.join(parts[len(level.path):]), index)
                    break
            else:
                self.add_field(column_name, index)

        # 3. Validate.
        for level in self.walk():
            where = f'level "{level.dotted_path()}"' if level.path else 'the root level'
            for field in level.key:
                if field not in level.fields:
                    raise RebuildSchemaError(
                        f'The key field "{field}" of {where} does not match any column. '
                        f'Available fields: {", ".join(level.fields) or "(none)"}.'
                    )
            for field in level.order_by:
                if field not in level.fields:
                    raise RebuildSchemaError(
                        f'The order_by field "{field}" of {where} does not match any column. '
                        f'Available fields: {", ".join(level.fields) or "(none)"}.'
                    )
            collisions = sorted(set(level.fields) & set(level.children))
            if collisions:
                raise RebuildSchemaError(
                    f'The rebuild schema is invalid: {", ".join(collisions)} used as both a field '
                    f'and a child array of {where}.'
                )

    def add_field(self, field, index):
        if field in self.fields:
            raise RebuildSchemaError(
                f'The rebuild schema assigns the field "{field}" of level "{self.dotted_path() or "(root)"}" '
                'to more than one column.'
            )
        self.fields[field] = index


def _canonical_key_value(value):
    """
    Reduce a key value to a canonical, JSON-serializable form, so that equal
    business identities produce identical node ids on every backend.
    """
    if isinstance(value, decimal.Decimal):
        # Normalize so that e.g. 2.5 and 2.50 identify the same object.
        return {'$number': str(value.normalize() if value else 0)}
    if isinstance(value, datetime.datetime):
        return {'$datetime': value.isoformat()}
    if isinstance(value, datetime.date):
        return {'$date': value.isoformat()}
    if isinstance(value, datetime.time):
        return {'$time': value.isoformat()}
    if isinstance(value, datetime.timedelta):
        return {'$timedelta': value.total_seconds()}
    return value


def _node_id(parent_id, level_name, key):
    return json.dumps(
        [parent_id, level_name, [_canonical_key_value(value) for value in key]],
        ensure_ascii=False,
        separators=(',', ':'),
    )


def _pack_value(value):
    """
    Encode a scalar value as JSON-native data without losing its type, for the
    on-disk store.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return _canonical_key_value(value)


def _unpack_value(value):
    if isinstance(value, dict) and len(value) == 1:
        if '$number' in value:
            return decimal.Decimal(value['$number'])
        if '$datetime' in value:
            return datetime.datetime.fromisoformat(value['$datetime'])
        if '$date' in value:
            return datetime.date.fromisoformat(value['$date'])
        if '$time' in value:
            return datetime.time.fromisoformat(value['$time'])
        if '$timedelta' in value:
            return datetime.timedelta(seconds=value['$timedelta'])
    return value


def _dump_node(node):
    return json.dumps({
        'id': node['id'],
        'parent': node['parent'],
        'level': node['level'],
        'seq': node['seq'],
        'created': node['created'],
        'fields': [[field, _pack_value(value)] for field, value in node['fields'].items()],
        'rows': node['rows'],
    }, ensure_ascii=False)


def _load_node(data):
    raw = json.loads(data)
    return {
        'id': raw['id'],
        'parent': raw['parent'],
        'level': raw['level'],
        'seq': raw['seq'],
        'created': raw['created'],
        'fields': OrderedDict((field, _unpack_value(value)) for field, value in raw['fields']),
        'rows': raw['rows'],
    }


class MemoryStore:
    """
    Keep the object index in memory.
    """

    def __init__(self):
        self.nodes = {}
        self.children_of = {}  # (parent id, level name) -> [node id], in insertion order

    def get(self, node_id):
        return self.nodes.get(node_id)

    def put(self, node):
        if node['id'] not in self.nodes:
            self.children_of.setdefault((node['parent'], node['level']), []).append(node['id'])
        self.nodes[node['id']] = node

    def iter_children(self, parent_id, level_name):
        for node_id in self.children_of.get((parent_id, level_name), []):
            yield self.nodes[node_id]

    def close(self):
        pass


class SQLiteStore:
    """
    Keep the object index in a temporary on-disk database, so that rebuilding
    large inputs does not exhaust memory.
    """

    def __init__(self, directory=None):
        self._tmp = tempfile.TemporaryDirectory(prefix='csvjson-rebuild-', dir=directory)
        self._db = sqlite3.connect(os.path.join(self._tmp.name, 'objects.db'))
        self._db.execute('PRAGMA journal_mode = OFF')
        self._db.execute('PRAGMA synchronous = OFF')
        self._db.execute(
            'CREATE TABLE objects ('
            'id TEXT PRIMARY KEY, '
            'parent TEXT NOT NULL, '
            'level TEXT NOT NULL, '
            'seq INTEGER NOT NULL, '
            'data TEXT NOT NULL)'
        )
        self._db.execute('CREATE INDEX objects_parent ON objects (parent, level, seq)')

    def get(self, node_id):
        row = self._db.execute('SELECT data FROM objects WHERE id = ?', (node_id,)).fetchone()
        return _load_node(row[0]) if row else None

    def put(self, node):
        data = _dump_node(node)
        try:
            self._db.execute(
                'INSERT INTO objects (id, parent, level, seq, data) VALUES (?, ?, ?, ?, ?)',
                (node['id'], node['parent'], node['level'], node['seq'], data),
            )
        except sqlite3.IntegrityError:
            self._db.execute('UPDATE objects SET data = ? WHERE id = ?', (data, node['id']))

    def iter_children(self, parent_id, level_name):
        cursor = self._db.execute(
            'SELECT data FROM objects WHERE parent = ? AND level = ? ORDER BY seq',
            (parent_id, level_name),
        )
        for (data,) in cursor:
            yield _load_node(data)

    def close(self):
        self._db.commit()
        self._db.close()
        self._tmp.cleanup()


def format_value(value):
    return json.dumps(value, default=default_float_decimal, ensure_ascii=False)


def order_key(node, fields):
    """
    A sort key for the declared order_by fields of a level. Nulls sort last;
    within a column, values share one inferred type, so they compare directly.
    """
    return tuple((True, 0) if node['fields'].get(field) is None else (False, node['fields'][field])
                 for field in fields)


class Rebuilder:
    """
    Fold denormalized rows into nested objects, as declared by a schema.
    """

    def __init__(self, schema, store, error_file, prog='csvjson'):
        self.schema = schema
        self.store = store
        self.error_file = error_file
        self.prog = prog
        self._seq = 0
        self._pending = []
        self.conflict_count = 0
        self.warning_count = 0

    def add_row(self, row_number, values):
        """
        Merge one input row. ``row_number`` is the 1-based number of the data
        row, reported in conflict and warning messages. ``values`` is a
        sequence aligned with the CSV columns.
        """
        level_fields = {}
        for level in self.schema.walk():
            level_fields[level.path] = OrderedDict(
                (field, values[index] if index < len(values) else None)
                for field, index in level.fields.items()
            )

        root_key = tuple(level_fields[()][field] for field in self.schema.key)
        if all(value is None for value in root_key):
            self._warn(f'row {row_number} skipped: all root key columns are null')
            return

        self._descend(self.schema, '', '$', level_fields, row_number)

    def _descend(self, level, parent_id, display, level_fields, row_number):
        fields = level_fields[level.path]

        if level.path and all(value is None for value in fields.values()):
            # A LEFT JOIN miss: the child is absent, which is not the same as a
            # child whose values are null. Deeper data without its parent is
            # orphaned and reported.
            orphan = self._first_present_descendant(level, level_fields)
            if orphan is not None:
                self._warn(
                    f'row {row_number}: ignoring {orphan.dotted_path()} values: {level.dotted_path()} is missing'
                )
            return

        key = tuple(fields[field] for field in level.key)
        node_id = _node_id(parent_id, level.name, key)
        display = self._display(display, level, key)

        node = self.store.get(node_id)
        if node is None:
            node = {
                'id': node_id,
                'parent': parent_id,
                'level': level.name,
                'seq': self._seq,
                'created': row_number,
                'fields': OrderedDict(),
                'rows': {},
            }
            self._seq += 1

        self._merge(node, fields, row_number, display)
        self.store.put(node)

        for child in level.children.values():
            self._descend(child, node_id, display, level_fields, row_number)

    def _display(self, parent_display, level, key):
        selector = '[{}]'.format(','.join(
            f'{field}={format_value(value)}' for field, value in zip(level.key, key)
        ))
        if level.path:
            return f'{parent_display}.{level.name}{selector}'
        return f'${selector}'

    def _first_present_descendant(self, level, level_fields):
        for descendant in level.walk():
            if descendant is level:
                continue
            if any(value is not None for value in level_fields[descendant.path].values()):
                return descendant
        return None

    def _merge(self, node, fields, row_number, display):
        for field, value in fields.items():
            if field not in node['fields']:
                node['fields'][field] = value
                node['rows'][field] = row_number
            elif value is not None:
                current = node['fields'][field]
                if current is None:
                    # A null never conflicts: a later non-null value completes it.
                    node['fields'][field] = value
                    node['rows'][field] = row_number
                elif current != value:
                    self.conflict_count += 1
                    if self.conflict_count <= MAX_REPORTED_CONFLICTS:
                        self._report(
                            f'{self.prog}: conflict at {display}.{field}: '
                            f'kept {format_value(current)} (row {node["rows"][field]}), '
                            f'ignored {format_value(value)} (row {row_number})\n'
                        )

    def _warn(self, message):
        self.warning_count += 1
        self._report(f'{self.prog}: warning: {message}\n')

    def _report(self, message):
        self._pending.append(message)
        if len(self._pending) >= _FLUSH_EVERY:
            self._flush()

    def _flush(self):
        if self._pending:
            self.error_file.write(''.join(self._pending))
            self._pending.clear()

    def iter_roots(self):
        """
        Assemble and yield the root objects, one at a time. Roots and children
        appear in first-seen order, unless the level declares ``order_by``.
        """
        self._flush()
        yield from self._iter_objects(self.schema, '')

    def _iter_objects(self, level, parent_id):
        nodes = list(self.store.iter_children(parent_id, level.name))
        if level.order_by:
            nodes.sort(key=lambda node: order_key(node, level.order_by))
        for node in nodes:
            obj = OrderedDict(node['fields'])
            for child in level.children.values():
                obj[child.name] = list(self._iter_objects(child, node['id']))
            yield obj

    def report_summary(self):
        if self.conflict_count:
            message = f'{self.conflict_count} scalar conflict(s) detected; kept the first-seen value'
            if self.conflict_count > MAX_REPORTED_CONFLICTS:
                message += f' (only the first {MAX_REPORTED_CONFLICTS} conflicts were reported)'
            self._warn(message)
        self._flush()
