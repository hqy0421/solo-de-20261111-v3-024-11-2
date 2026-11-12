#!/usr/bin/env python

import json
import sys
import textwrap
from collections import OrderedDict

import agate
from agate.data_types.base import DEFAULT_NULL_VALUES

from csvkit.cli import CSVKitUtility, default_float_decimal, default_str_decimal, match_column_identifier
from csvkit.rebuild import MemoryStore, Rebuilder, RebuildSchemaError, SQLiteStore, load_schema


class CSVJSON(CSVKitUtility):
    description = 'Convert a CSV file into JSON (or GeoJSON).'

    def add_arguments(self):
        self.argparser.add_argument(
            '-i', '--indent', dest='indent', type=int,
            help='Indent the output JSON this many spaces. Disabled by default.')
        self.argparser.add_argument(
            '-k', '--key', dest='key',
            help='Output JSON as an object keyed by a given column, KEY, rather than as an array. All column values '
                 'must be unique. If --lat and --lon are specified, this column is used as the GeoJSON Feature ID.')
        self.argparser.add_argument(
            '--lat', dest='lat',
            help='A column index or name containing a latitude. Output will be GeoJSON instead of JSON. '
                 'Requires --lon.')
        self.argparser.add_argument(
            '--lon', dest='lon',
            help='A column index or name containing a longitude. Output will be GeoJSON instead of JSON. '
                 'Requires --lat.')
        self.argparser.add_argument(
            '--type', dest='type',
            help='A column index or name containing a GeoJSON type. Output will be GeoJSON instead of JSON. '
                 'Requires --lat and --lon.')
        self.argparser.add_argument(
            '--geometry', dest='geometry',
            help='A column index or name containing a GeoJSON geometry. Output will be GeoJSON instead of JSON. '
                 'Requires --lat and --lon.')
        self.argparser.add_argument(
            '--crs', dest='crs',
            help='A coordinate reference system string to be included with GeoJSON output. Requires --lat and --lon.')
        self.argparser.add_argument(
            '--no-bbox', dest='no_bbox', action='store_true',
            help='Disable the calculation of a bounding box.')
        self.argparser.add_argument(
            '--stream', dest='streamOutput', action='store_true',
            help='Output JSON as a stream of newline-separated objects, rather than an as an array.')
        self.argparser.add_argument(
            '--rebuild', dest='rebuild', metavar='SCHEMA',
            help='Rebuild nested JSON from a denormalized CSV (such as a join result), using a JSON schema file '
                 'that declares the parent-child paths and the composite key of each level. Rows are merged by '
                 'business identity instead of producing one object per row.')
        self.argparser.add_argument(
            '--spill', dest='spill', action='store_true',
            help='With --rebuild: keep the object index in a temporary on-disk database instead of in memory, '
                 'for large inputs. The temporary directory is chosen via TMPDIR.')
        self.argparser.add_argument(
            '-y', '--snifflimit', dest='sniff_limit', type=int, default=1024,
            help='Limit CSV dialect sniffing to the specified number of bytes. '
                 'Specify "0" to disable sniffing entirely, or "-1" to sniff the entire file.')
        self.argparser.add_argument(
            '-I', '--no-inference', dest='no_inference', action='store_true',
            help='Disable type inference (and --locale, --date-format, --datetime-format, --no-leading-zeroes) '
                 'when parsing the input.')

    def main(self):
        """
        Convert CSV to JSON.
        """
        if self.args.lat and not self.args.lon:
            self.argparser.error('--lon is required whenever --lat is specified.')
        if self.args.lon and not self.args.lat:
            self.argparser.error('--lat is required whenever --lon is specified.')

        if self.args.crs and not self.args.lat:
            self.argparser.error('--crs is only allowed when --lat and --lon are also specified.')
        if self.args.type and not self.args.lat:
            self.argparser.error('--type is only allowed when --lat and --lon are also specified.')
        if self.args.geometry and not self.args.lat:
            self.argparser.error('--geometry is only allowed when --lat and --lon are also specified.')

        if self.args.key and self.args.streamOutput and not (self.args.lat and self.args.lon):
            self.argparser.error('--key is only allowed with --stream when --lat and --lon are also specified.')

        if self.args.rebuild:
            if self.args.key:
                self.argparser.error('--key is not allowed with --rebuild. The root key is declared in the schema.')
            if self.is_geo():
                self.argparser.error('--lat and --lon are not allowed with --rebuild.')
        elif self.args.spill:
            self.argparser.error('--spill is only allowed with --rebuild.')

        self.json_kwargs = {
            'indent': self.args.indent,
        }

        if self.args.rebuild:
            if self.additional_input_expected():
                self.argparser.error('You must provide an input file or piped data.')

            self.output_rebuild()
            return

        if self.can_stream():
            if self.additional_input_expected():
                sys.stderr.write('No input file or piped data provided. Waiting for standard input:\n')

            if self.is_geo():
                self.streaming_output_ndgeojson()
            else:
                self.streaming_output_ndjson()

        else:
            if self.additional_input_expected():
                self.argparser.error('You must provide an input file or piped data.')

            if self.is_geo():
                self.output_geojson()
            else:
                self.output_json()

    def dump_json(self, data, newline=False):
        json.dump(data, self.output_file, default=default_str_decimal, ensure_ascii=False, **self.json_kwargs)
        if newline:
            self.output_file.write("\n")

    def can_stream(self):
        return (
            self.args.streamOutput
            and self.args.no_inference
            and self.args.sniff_limit == 0
            and not self.args.skip_lines
        )

    def is_geo(self):
        return self.args.lat and self.args.lon

    def read_csv_to_table(self):
        sniff_limit = self.args.sniff_limit if self.args.sniff_limit != -1 else None
        return agate.Table.from_csv(
            self.input_file,
            skip_lines=self.args.skip_lines,
            sniff_limit=sniff_limit,
            column_types=self.get_column_types(),
            **self.reader_kwargs,
        )

    def output_json(self):
        self.read_csv_to_table().to_json(
            self.output_file,
            key=self.args.key,
            newline=self.args.streamOutput,
            indent=self.args.indent,
        )

    def output_rebuild(self):
        """
        Merge a denormalized CSV into nested JSON objects, as declared by the
        --rebuild schema.
        """
        if self.args.no_inference and self.args.sniff_limit == 0 and not self.args.skip_lines:
            # Stream the input with constant memory, like streaming_output_ndjson.
            text_type = self.get_text_type()
            rows = agate.csv.reader(self.input_file, **self.reader_kwargs)
            column_names = next(rows)

            def iter_rows():
                for row_number, row in enumerate(rows, 1):
                    yield row_number, [text_type.cast(value) for value in row]
        else:
            table = self.read_csv_to_table()
            column_names = table.column_names

            def iter_rows():
                for row_number, row in enumerate(table.rows, 1):
                    yield row_number, row

        try:
            schema = load_schema(self.args.rebuild)
            schema.resolve_columns(column_names, self.get_column_offset())
        except RebuildSchemaError as e:
            self.argparser.error(str(e))

        store = SQLiteStore() if self.args.spill else MemoryStore()
        try:
            rebuilder = Rebuilder(schema, store, self.error_file, prog=type(self).__name__.lower())

            for row_number, values in iter_rows():
                rebuilder.add_row(row_number, values)

            if self.args.streamOutput:
                for obj in rebuilder.iter_roots():
                    self.dump_rebuild_object(obj, newline=True)
            else:
                self.dump_rebuild_array(rebuilder.iter_roots())

            rebuilder.report_summary()
        finally:
            store.close()

    def get_text_type(self):
        """
        The agate.Text instance used to detect nulls when streaming raw rows,
        mirroring the null handling of get_column_types.
        """
        if getattr(self.args, 'blanks', None):
            null_values = []
        else:
            null_values = list(DEFAULT_NULL_VALUES)
        null_values.extend(getattr(self.args, 'null_values', None) or [])

        return agate.Text(null_values=null_values)

    def dump_rebuild_object(self, data, newline=False):
        json.dump(data, self.output_file, default=default_float_decimal, ensure_ascii=False, **self.json_kwargs)
        if newline:
            self.output_file.write("\n")

    def dump_rebuild_array(self, roots):
        """
        Write the root objects as one JSON array, streaming root by root so
        that large results are not held in memory.
        """
        indent = self.args.indent

        if indent is None:
            self.output_file.write('[')
            for i, obj in enumerate(roots):
                if i:
                    self.output_file.write(', ')
                self.dump_rebuild_object(obj)
            self.output_file.write(']')
            return

        pad = ' ' * indent
        first = True
        for obj in roots:
            if first:
                self.output_file.write('[\n')
                first = False
            else:
                self.output_file.write(',\n')
            text = json.dumps(obj, default=default_float_decimal, ensure_ascii=False, **self.json_kwargs)
            self.output_file.write(textwrap.indent(text, pad))
        if first:
            self.output_file.write('[]')
        else:
            self.output_file.write('\n]')

    def output_geojson(self):
        table = self.read_csv_to_table()
        geojson_generator = self.GeoJsonGenerator(self.args, table.column_names)

        if self.args.streamOutput:
            for row in table.rows:
                self.dump_json(geojson_generator.feature_for_row(row), newline=True)
        else:
            self.dump_json(geojson_generator.generate_feature_collection(table))

    def streaming_output_ndjson(self):
        rows = agate.csv.reader(self.input_file, **self.reader_kwargs)
        column_names = next(rows)

        for row in rows:
            data = OrderedDict()
            for i, column in enumerate(column_names):
                try:
                    data[column] = row[i]
                except IndexError:
                    data[column] = None
            self.dump_json(data, newline=True)

    def streaming_output_ndgeojson(self):
        rows = agate.csv.reader(self.input_file, **self.reader_kwargs)
        column_names = next(rows)
        geojson_generator = self.GeoJsonGenerator(self.args, column_names)

        for row in rows:
            self.dump_json(geojson_generator.feature_for_row(row), newline=True)

    class GeoJsonGenerator:
        def __init__(self, args, column_names):
            self.args = args
            self.column_names = column_names

            self.lat_column = match_column_identifier(column_names, self.args.lat, self.args.zero_based)

            self.lon_column = match_column_identifier(column_names, self.args.lon, self.args.zero_based)

            if self.args.type:
                self.type_column = match_column_identifier(column_names, self.args.type, self.args.zero_based)
            else:
                self.type_column = None

            if self.args.geometry:
                self.geometry_column = match_column_identifier(column_names, self.args.geometry, self.args.zero_based)
            else:
                self.geometry_column = None

            if self.args.key:
                self.id_column = match_column_identifier(column_names, self.args.key, self.args.zero_based)
            else:
                self.id_column = None

        def generate_feature_collection(self, table):
            features = []
            bounds = self.GeoJsonBounds()

            for row in table.rows:
                feature = self.feature_for_row(row)

                if not self.args.no_bbox:
                    bounds.add_feature(feature)

                features.append(feature)

            items = [
                ('type', 'FeatureCollection'),
                ('features', features),
            ]

            if not self.args.no_bbox and bounds.min_lon is not None:
                items.insert(1, ('bbox', bounds.bbox()))

            if self.args.crs:
                items.append(('crs', OrderedDict([
                    ('type', 'name'),
                    ('properties', {
                        'name': self.args.crs,
                    }),
                ])))

            return OrderedDict(items)

        def feature_for_row(self, row):
            feature = OrderedDict([
                ('type', 'Feature'),
                ('properties', OrderedDict()),
            ])

            for i, c in enumerate(row):
                # Prevent "type" or geo fields from being added to properties.
                if c is None or i in (self.type_column, self.lat_column, self.lon_column, self.geometry_column):
                    continue
                if i == self.id_column:
                    feature['id'] = c
                elif c:
                    feature['properties'][self.column_names[i]] = c

            feature['geometry'] = self.geometry_for_row(row)

            return feature

        def geometry_for_row(self, row):
            lat = None
            lon = None

            if self.geometry_column is not None:
                return json.loads(row[self.geometry_column])

            if self.lat_column is not None and self.lon_column is not None:
                try:
                    lon = float(row[self.lon_column])
                    lat = float(row[self.lat_column])
                except (TypeError, ValueError):
                    lon = None
                    lat = None

            if lon is not None and lat is not None:
                return OrderedDict([
                    ('type', 'Point'),
                    ('coordinates', [lon, lat]),
                ])

        class GeoJsonBounds:
            def __init__(self):
                self.min_lon = None
                self.min_lat = None
                self.max_lon = None
                self.max_lat = None

            def bbox(self):
                return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

            def add_feature(self, feature):
                if feature.get('geometry') and 'coordinates' in feature['geometry']:
                    self.update_coordinates(feature['geometry']['coordinates'])

            def update_lat(self, lat):
                if self.min_lat is None or lat < self.min_lat:
                    self.min_lat = lat
                if self.max_lat is None or lat > self.max_lat:
                    self.max_lat = lat

            def update_lon(self, lon):
                if self.min_lon is None or lon < self.min_lon:
                    self.min_lon = lon
                if self.max_lon is None or lon > self.max_lon:
                    self.max_lon = lon

            def update_coordinates(self, coordinates):
                if len(coordinates) <= 3 and isinstance(coordinates[0], (float, int)):
                    self.update_lon(coordinates[0])
                    self.update_lat(coordinates[1])
                else:
                    for coordinate in coordinates:
                        self.update_coordinates(coordinate)


def launch_new_instance():
    utility = CSVJSON()
    utility.run()


if __name__ == '__main__':
    launch_new_instance()
