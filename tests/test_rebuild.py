#!/usr/bin/env python
import datetime
import decimal
import io
import unittest

from csvkit.rebuild import (Level, MemoryStore, Rebuilder, RebuildSchemaError, SQLiteStore, _canonical_key_value,
                            _node_id, order_key)


class TestLevel(unittest.TestCase):
    def test_requires_object(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'must be a JSON object'):
            Level('', ['key'], ())

    def test_requires_key(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'must declare a "key"'):
            Level('', {}, ())

    def test_rejects_empty_key(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'must not declare an empty "key"'):
            Level('', {'key': []}, ())

    def test_rejects_unknown_option(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'unknown option'):
            Level('', {'key': 'a', 'keys': ['b']}, ())

    def test_rejects_bad_key_type(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'must be a string or a list of strings'):
            Level('', {'key': 1}, ())

    def test_rejects_dotted_child_name(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'must not contain dots'):
            Level('', {'key': 'a', 'children': {'x.y': {'key': 'b'}}}, ())

    def test_key_string_becomes_list(self):
        level = Level('', {'key': 'a'}, ())
        self.assertEqual(level.key, ['a'])


class TestResolveColumns(unittest.TestCase):
    def resolve(self, spec, column_names, offset=1):
        level = Level('', spec, ())
        level.resolve_columns(column_names, offset)
        return level

    def test_dotted_prefixes(self):
        level = self.resolve(
            {'key': 'a', 'children': {'items': {'key': 'n', 'children': {'parts': {'key': 'p'}}}}},
            ['a', 'b', 'items.n', 'items.parts.p', 'items.parts.x'],
        )
        self.assertEqual(list(level.fields), ['a', 'b'])
        self.assertEqual(list(level.children['items'].fields), ['n'])
        self.assertEqual(list(level.children['items'].children['parts'].fields), ['p', 'x'])

    def test_unprefixed_columns_go_to_root(self):
        level = self.resolve({'key': 'a', 'children': {'items': {'key': 'n'}}}, ['items.n', 'a', 'z'])
        self.assertEqual(list(level.fields), ['a', 'z'])

    def test_unknown_prefix_goes_to_root(self):
        level = self.resolve({'key': 'a'}, ['other.x', 'a'])
        self.assertEqual(list(level.fields), ['other.x', 'a'])

    def test_explicit_columns_rename(self):
        level = self.resolve(
            {'key': 'ident', 'columns': {'a': 'ident'}, 'children': {'items': {'key': 'n', 'columns': {'b': 'n'}}}},
            ['a', 'b'],
        )
        self.assertEqual(level.fields, {'ident': 0})
        self.assertEqual(level.children['items'].fields, {'n': 1})

    def test_explicit_columns_by_index(self):
        level = self.resolve({'key': 'ident', 'columns': {'1': 'ident'}}, ['a'], offset=1)
        self.assertEqual(level.fields, {'ident': 0})
        level = self.resolve({'key': 'ident', 'columns': {'0': 'ident'}}, ['a'], offset=0)
        self.assertEqual(level.fields, {'ident': 0})

    def test_explicit_columns_unknown_column(self):
        with self.assertRaisesRegex(RebuildSchemaError, "Column 'nope' is invalid"):
            self.resolve({'key': 'a', 'columns': {'nope': 'x'}}, ['a'])

    def test_duplicate_column_claim(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'more than once'):
            self.resolve(
                {'key': 'a', 'columns': {'b': 'x'}, 'children': {'items': {'key': 'y', 'columns': {'b': 'y'}}}},
                ['a', 'b'],
            )

    def test_unknown_key_field(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'The key field "nope" of the root level'):
            self.resolve({'key': 'nope'}, ['a'])

    def test_unknown_order_by_field(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'The order_by field "nope"'):
            self.resolve({'key': 'a', 'order_by': 'nope'}, ['a'])

    def test_field_child_collision(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'both a field and a child array'):
            self.resolve({'key': 'a', 'children': {'items': {'key': 'n'}}}, ['a', 'items', 'items.n'])

    def test_duplicate_field_assignment(self):
        with self.assertRaisesRegex(RebuildSchemaError, 'more than one column'):
            self.resolve(
                {'key': 'a', 'children': {'items': {'key': 'n', 'columns': {'x': 'n'}}}},
                ['a', 'items.n', 'x'],
            )


class TestIdentity(unittest.TestCase):
    def test_decimal_key_is_canonical(self):
        a = _node_id('', 'items', [decimal.Decimal('2.5')])
        b = _node_id('', 'items', [decimal.Decimal('2.50')])
        self.assertEqual(a, b)

    def test_zero_decimal_key_is_canonical(self):
        self.assertEqual(
            _node_id('', 'items', [decimal.Decimal('0.00')]),
            _node_id('', 'items', [decimal.Decimal('-0')]),
        )

    def test_typed_keys_do_not_collide(self):
        self.assertNotEqual(_node_id('', 'items', [1]), _node_id('', 'items', ['1']))
        self.assertNotEqual(_node_id('', 'items', [True]), _node_id('', 'items', [1]))

    def test_date_key(self):
        value = _canonical_key_value(datetime.date(2024, 5, 1))
        self.assertEqual(value, {'$date': '2024-05-01'})


class TestOrderKey(unittest.TestCase):
    def test_nulls_sort_last(self):
        nodes = [
            {'fields': {'n': 2.0}},
            {'fields': {'n': None}},
            {'fields': {'n': 1.0}},
        ]
        nodes.sort(key=lambda node: order_key(node, ['n']))
        self.assertEqual([node['fields']['n'] for node in nodes], [1.0, 2.0, None])


class RebuildStoreTests:
    def make_store(self):
        raise NotImplementedError

    def build(self, rows, spec=None):
        column_names = ['id', 'name', 'items.n', 'items.v']
        spec = spec or {'key': 'id', 'children': {'items': {'key': 'n', 'order_by': 'n'}}}
        schema = Level('', spec, ())
        schema.resolve_columns(column_names)
        store = self.make_store()
        errors = io.StringIO()
        rebuilder = Rebuilder(schema, store, errors)
        for number, row in enumerate(rows, 1):
            rebuilder.add_row(number, row)
        try:
            roots = list(rebuilder.iter_roots())
        finally:
            store.close()
        return roots, errors.getvalue()

    def test_merge_and_order(self):
        roots, errors = self.build([
            ['b', 'B', '2', 'x'],
            ['a', 'A', '1', 'y'],
            ['b', 'B', '1', 'z'],
            ['b', 'B', '2', 'x'],  # exact duplicate: no conflict
        ])
        self.assertEqual(errors, '')
        self.assertEqual(roots, [
            {'id': 'b', 'name': 'B', 'items': [{'n': '1', 'v': 'z'}, {'n': '2', 'v': 'x'}]},
            {'id': 'a', 'name': 'A', 'items': [{'n': '1', 'v': 'y'}]},
        ])

    def test_root_order_by(self):
        roots, _ = self.build(
            [
                ['b', 'B', '1', 'x'],
                ['a', 'A', '2', 'y'],
            ],
            spec={'key': 'id', 'order_by': 'id', 'children': {'items': {'key': 'n'}}},
        )
        self.assertEqual([root['id'] for root in roots], ['a', 'b'])

    def test_missing_child_and_null_value(self):
        roots, _ = self.build([
            ['a', 'A', None, None],  # no item at all
            ['b', 'B', '1', None],  # an item with a null value
        ])
        self.assertEqual(roots, [
            {'id': 'a', 'name': 'A', 'items': []},
            {'id': 'b', 'name': 'B', 'items': [{'n': '1', 'v': None}]},
        ])

    def test_null_completes_earlier_null(self):
        roots, errors = self.build([
            ['a', None, '1', None],
            ['a', 'A', '1', 'x'],
        ])
        self.assertEqual(errors, '')
        self.assertEqual(roots, [{'id': 'a', 'name': 'A', 'items': [{'n': '1', 'v': 'x'}]}])

    def test_conflict_reports_rows(self):
        roots, errors = self.build([
            ['a', 'A', '1', 'x'],
            ['a', 'B', '1', 'y'],
        ])
        self.assertEqual(roots[0]['name'], 'A')
        self.assertEqual(roots[0]['items'][0]['v'], 'x')
        self.assertIn('conflict at $[id="a"].name: kept "A" (row 1), ignored "B" (row 2)', errors)
        self.assertIn('conflict at $[id="a"].items[n="1"].v: kept "x" (row 1), ignored "y" (row 2)', errors)

    def test_null_key_component_merges(self):
        roots, errors = self.build([
            ['a', 'A', None, 'x'],
            ['a', 'A', None, 'x'],
        ])
        self.assertEqual(errors, '')
        self.assertEqual(len(roots[0]['items']), 1)

    def test_ragged_row(self):
        roots, errors = self.build([
            ['a', 'A'],  # short row: item columns are missing
        ])
        self.assertEqual(errors, '')
        self.assertEqual(roots, [{'id': 'a', 'name': 'A', 'items': []}])

    def test_typed_values_round_trip(self):
        roots, _ = self.build([
            [1, decimal.Decimal('2.5'), datetime.date(2024, 5, 1), True],
        ], spec={'key': 'id', 'children': {'items': {'key': 'n'}}})
        self.assertEqual(roots, [{
            'id': 1,
            'name': decimal.Decimal('2.5'),
            'items': [{'n': datetime.date(2024, 5, 1), 'v': True}],
        }])


class TestRebuildMemoryStore(unittest.TestCase, RebuildStoreTests):
    def make_store(self):
        return MemoryStore()


class TestRebuildSQLiteStore(unittest.TestCase, RebuildStoreTests):
    def make_store(self):
        return SQLiteStore()
