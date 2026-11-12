import io
import json
import sys
from contextlib import redirect_stderr
from unittest.mock import patch

from csvkit.utilities.csvjson import CSVJSON, launch_new_instance
from tests.utils import CSVKitTestCase, EmptyFileTests


class TestCSVJSON(CSVKitTestCase, EmptyFileTests):
    Utility = CSVJSON

    def get_output_and_error(self, args):
        output_file = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='', write_through=True)
        error_file = io.StringIO()

        utility = self.Utility(args, output_file, error_file)
        utility.run()

        output = output_file.buffer.getvalue().decode('utf-8')
        output_file.close()

        return output, error_file.getvalue()

    def test_launch_new_instance(self):
        with patch.object(sys, 'argv', [self.Utility.__name__.lower(), 'examples/dummy.csv']):
            launch_new_instance()

    def test_options(self):
        self.assertError(
            launch_new_instance,
            ['--key', 'value', '--stream'],
            '--key is only allowed with --stream when --lat and --lon are also specified.',
        )

    def test_latlon_options(self):
        for option, message in (
            ('lat', '--lon is required whenever --lat is specified.'),
            ('lon', '--lat is required whenever --lon is specified.'),
            ('crs', '--crs is only allowed when --lat and --lon are also specified.'),
            ('type', '--type is only allowed when --lat and --lon are also specified.'),
            ('geometry', '--geometry is only allowed when --lat and --lon are also specified.'),
        ):
            with self.subTest(option=option):
                self.assertError(launch_new_instance, [f'--{option}', 'value'], message)

    def test_simple(self):
        js = json.loads(self.get_output(['examples/dummy.csv']))
        self.assertDictEqual(js[0], {'a': True, 'c': 3.0, 'b': 2.0})

    def test_sniff_limit(self):
        js = json.loads(self.get_output(['examples/sniff_limit.csv']))
        self.assertDictEqual(js[0], {'a': True, 'c': 3.0, 'b': 2.0})

    def test_tsv(self):
        js = json.loads(self.get_output(['examples/dummy.tsv']))
        self.assertDictEqual(js[0], {'a': True, 'c': 3.0, 'b': 2.0})

    def test_tsv_streaming(self):
        js = json.loads(self.get_output(['--stream', '--no-inference',
                        '--snifflimit', '0', '--tabs', 'examples/dummy.tsv']))
        self.assertDictEqual(js, {'a': '1', 'c': '3', 'b': '2'})

    def test_no_blanks(self):
        js = json.loads(self.get_output(['examples/blanks.csv']))
        self.assertDictEqual(js[0], {'a': None, 'b': None, 'c': None, 'd': None, 'e': None, 'f': None})

    def test_blanks(self):
        js = json.loads(self.get_output(['--blanks', 'examples/blanks.csv']))
        self.assertDictEqual(js[0], {'a': '', 'b': 'NA', 'c': 'N/A', 'd': 'NONE', 'e': 'NULL', 'f': '.'})

    def test_no_header_row(self):
        js = json.loads(self.get_output(['--no-header-row', 'examples/no_header_row.csv']))
        self.assertDictEqual(js[0], {'a': True, 'c': 3.0, 'b': 2.0})

    def test_no_inference(self):
        js = json.loads(self.get_output(['--no-inference', 'examples/dummy.csv']))
        self.assertDictEqual(js[0], {'a': '1', 'c': '3', 'b': '2'})

    def test_indentation(self):
        output = self.get_output(['-i', '4', 'examples/dummy.csv'])
        js = json.loads(output)
        self.assertDictEqual(js[0], {'a': True, 'c': 3.0, 'b': 2.0})
        self.assertRegex(output, '        "a": true,')

    def test_keying(self):
        js = json.loads(self.get_output(['-k', 'a', 'examples/dummy.csv']))
        self.assertDictEqual(js, {'True': {'a': True, 'c': 3.0, 'b': 2.0}})

    def test_duplicate_keys(self):
        output_file = io.StringIO()
        utility = CSVJSON(['-k', 'a', 'examples/dummy3.csv'], output_file)
        self.assertRaisesRegex(ValueError,
                               'Value True is not unique in the key column.',
                               utility.run)
        output_file.close()

    def test_geojson_with_id(self):
        geojson = json.loads(self.get_output(['--lat', 'latitude', '--lon',
                             'longitude', '-k', 'slug', 'examples/test_geo.csv']))

        self.assertEqual(geojson['type'], 'FeatureCollection')
        self.assertNotIn('crs', geojson)
        self.assertEqual(geojson['bbox'], [-95.334619, 32.299076986939205, -95.250699, 32.351434])
        self.assertEqual(len(geojson['features']), 17)

        for feature in geojson['features']:
            self.assertEqual(feature['type'], 'Feature')
            self.assertIn('id', feature)
            self.assertIn('properties', feature)
            self.assertIsInstance(feature['properties'], dict)
            self.assertGreater(len(feature['properties']), 1)

            geometry = feature['geometry']

            self.assertEqual(len(geometry['coordinates']), 2)
            self.assertIsInstance(geometry['coordinates'][0], float)
            self.assertIsInstance(geometry['coordinates'][1], float)

    def test_geojson_point(self):
        geojson = json.loads(self.get_output(['--lat', 'latitude', '--lon', 'longitude', 'examples/test_geo.csv']))

        self.assertEqual(geojson['type'], 'FeatureCollection')
        self.assertNotIn('crs', geojson)
        self.assertEqual(geojson['bbox'], [-95.334619, 32.299076986939205, -95.250699, 32.351434])
        self.assertEqual(len(geojson['features']), 17)

        for feature in geojson['features']:
            self.assertEqual(feature['type'], 'Feature')
            self.assertNotIn('id', feature)
            self.assertIn('properties', feature)
            self.assertIsInstance(feature['properties'], dict)
            self.assertGreater(len(feature['properties']), 1)

            geometry = feature['geometry']

            self.assertEqual(len(geometry['coordinates']), 2)
            self.assertIsInstance(geometry['coordinates'][0], float)
            self.assertIsInstance(geometry['coordinates'][1], float)

    def test_geojson_no_coordinates(self):
        geojson = json.loads(self.get_output(['--lat', 'latitude', '--lon', 'longitude',
                             'examples/test_geo_no_coordinates.csv']))

        self.assertNotIn('bbox', geojson)

    def test_geojson_missing_coordinates(self):
        geojson = json.loads(self.get_output(['--lat', 'latitude', '--lon', 'longitude',
                             'examples/test_geo_null.csv']))

        self.assertEqual(geojson['bbox'], [-95.28174, 32.33396, -95.28174, 32.33396])
        self.assertEqual(len(geojson['features']), 2)
        self.assertIsNone(geojson['features'][1]['geometry'])

    def test_geojson_zero_coordinates(self):
        geojson = json.loads(self.get_output(['--lat', 'latitude', '--lon', 'longitude',
                             'examples/test_geo_zero.csv']))

        self.assertEqual(geojson['bbox'], [0.0, 0.0, 2.35, 51.48])
        self.assertEqual(len(geojson['features']), 3)
        self.assertEqual(geojson['features'][0]['geometry']['coordinates'], [0.0, 0.0])
        self.assertEqual(geojson['features'][1]['geometry']['coordinates'], [0.0, 51.48])
        self.assertEqual(geojson['features'][2]['geometry']['coordinates'], [2.35, 0.0])

    def test_geojson_shape(self):
        geojson = json.loads(self.get_output(['--lat', 'latitude', '--lon', 'longitude',
                             '--type', 'type', '--geometry', 'geojson', 'examples/test_geojson.csv']))

        self.assertEqual(geojson['type'], 'FeatureCollection')
        self.assertNotIn('crs', geojson)
        self.assertEqual(geojson['bbox'], [100.0, 0.0, 105.0, 1.0])
        self.assertEqual(len(geojson['features']), 3)

        for feature in geojson['features']:
            self.assertEqual(feature['type'], 'Feature')
            self.assertNotIn('id', feature)
            self.assertIn('properties', feature)
            self.assertIsInstance(feature['properties'], dict)
            self.assertIn('prop0', feature['properties'].keys())

            geometry = feature['geometry']

            self.assertIn('coordinates', geometry)
            self.assertIsNotNone(geometry['coordinates'])

        self.assertEqual(geojson['features'][0]['geometry']['type'], 'Point')
        self.assertEqual(geojson['features'][1]['geometry']['type'], 'LineString')
        self.assertEqual(geojson['features'][2]['geometry']['type'], 'Polygon')

        self.assertEqual(geojson['features'][0]['geometry']['coordinates'], [102.0, 0.5])
        self.assertEqual(geojson['features'][1]['geometry']['coordinates'],
                         [[102.0, 0.0], [103.0, 1.0], [104.0, 0.0], [105.0, 1.0]])
        self.assertEqual(geojson['features'][2]['geometry']['coordinates'],
                         [[[100.0, 0.0], [101.0, 0.0], [101.0, 1.0], [100.0, 1.0], [100.0, 0.0]]])

    def test_geojson_with_crs(self):
        geojson = json.loads(self.get_output(['--lat', 'latitude', '--lon',
                             'longitude', '--crs', 'EPSG:4269', 'examples/test_geo.csv']))
        self.assertIn('crs', geojson)
        self.assertEqual(geojson['crs'], {'type': 'name', 'properties': {'name': 'EPSG:4269'}})

    def test_geojson_with_no_bbox(self):
        geojson = json.loads(self.get_output(['--lat', 'latitude', '--lon',
                             'longitude', '--no-bbox', 'examples/test_geo.csv']))
        self.assertNotIn('bbox', geojson)

    def test_ndjson(self):
        self.assertLines(['--stream', 'examples/testjson_converted.csv'], [
            '{"text": "Chicago Reader", "float": 1.0, "datetime": "1971-01-01T04:14:00", "boolean": true, "time": "4:14:00", "date": "1971-01-01", "integer": 40.0}',  # noqa: E501
            '{"text": "Chicago Sun-Times", "float": 1.27, "datetime": "1948-01-01T14:57:13", "boolean": true, "time": "14:57:13", "date": "1948-01-01", "integer": 63.0}',  # noqa: E501
            '{"text": "Chicago Tribune", "float": 41800000.01, "datetime": "1920-01-01T00:00:00", "boolean": false, "time": "0:00:00", "date": "1920-01-01", "integer": 164.0}',  # noqa: E501
            '{"text": "This row has blanks", "float": null, "datetime": null, "boolean": null, "time": null, "date": null, "integer": null}',  # noqa: E501
            '{"text": "Unicode! Σ", "float": null, "datetime": null, "boolean": null, "time": null, "date": null, "integer": null}',  # noqa: E501
        ])

    def test_ndjson_streaming(self):
        self.assertLines(['--stream', '--no-inference', '--snifflimit', '0', 'examples/testjson_converted.csv'], [
            '{"text": "Chicago Reader", "float": "1.0", "datetime": "1971-01-01T04:14:00", "boolean": "True", "time": "4:14:00", "date": "1971-01-01", "integer": "40"}',  # noqa: E501
            '{"text": "Chicago Sun-Times", "float": "1.27", "datetime": "1948-01-01T14:57:13", "boolean": "True", "time": "14:57:13", "date": "1948-01-01", "integer": "63"}',  # noqa: E501
            '{"text": "Chicago Tribune", "float": "41800000.01", "datetime": "1920-01-01T00:00:00", "boolean": "False", "time": "0:00:00", "date": "1920-01-01", "integer": "164"}',  # noqa: E501
            '{"text": "This row has blanks", "float": "", "datetime": "", "boolean": "", "time": "", "date": "", "integer": ""}',  # noqa: E501
            '{"text": "Unicode! Σ", "float": "", "datetime": "", "boolean": "", "time": "", "date": "", "integer": ""}',  # noqa: E501
        ])

    def test_ndgeojson(self):
        self.maxDiff = 5000
        self.assertLines(['--lat', 'latitude', '--lon', 'longitude', '--stream', 'examples/test_geo.csv'], [
            '{"type": "Feature", "properties": {"slug": "dcl", "title": "Downtown Coffee Lounge", "description": "In addition to being the only coffee shop in downtown Tyler, DCL also features regular exhibitions of work by local artists.", "address": "200 West Erwin Street", "type": "Gallery", "last_seen_date": "2012-03-30"}, "geometry": {"type": "Point", "coordinates": [-95.30181, 32.35066]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "tyler-museum", "title": "Tyler Museum of Art", "description": "The Tyler Museum of Art on the campus of Tyler Junior College is the largest art museum in Tyler. Visit them online at <a href=\\"http://www.tylermuseum.org/\\">http://www.tylermuseum.org/</a>.", "address": "1300 South Mahon Avenue", "type": "Museum", "last_seen_date": "2012-04-02"}, "geometry": {"type": "Point", "coordinates": [-95.28174, 32.33396]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "genecov", "title": "Genecov Sculpture", "description": "Stainless Steel Sculpture", "address": "1350 Dominion Plaza", "type": "Sculpture", "photo_url": "http://i.imgur.com/DICdn.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-04"}, "geometry": {"type": "Point", "coordinates": [-95.31571447849274, 32.299076986939205]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "gallery-main-street", "title": "Gallery Main Street", "description": "The only dedicated art gallery in Tyler. Visit them online at <a href=\\"http://www.heartoftyler.com/downtowntylerarts/\\">http://www.heartoftyler.com/downtowntylerarts/</a>.", "address": "110 West Erwin Street", "type": "Gallery", "last_seen_date": "2012-04-09"}, "geometry": {"type": "Point", "coordinates": [-95.30123, 32.35066]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "spirit-of-progress", "title": "The Spirit of Progress", "address": "100 block of North Spring Avenue", "type": "Relief", "photo_url": "http://media.hacktyler.com/artmap/photos/spirit-of-progress.jpg", "photo_credit": "Photo by Christopher Groskopf. Used with permission.", "last_seen_date": "2012-04-11"}, "geometry": {"type": "Point", "coordinates": [-95.2995, 32.3513]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "celestial-conversations-2", "title": "Celestial Conversations #2", "artist": "Simon Saleh", "description": "Steel Sculpture", "address": "100 block of North Spring Avenue", "type": "Sculpture", "photo_url": "http://media.hacktyler.com/artmap/photos/celestial-conversations-2.jpg", "photo_credit": "Photo by Christopher Groskopf. Used with permission.", "last_seen_date": "2012-04-11"}, "geometry": {"type": "Point", "coordinates": [-95.2995, 32.351]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "pivot-bounce", "title": "Pivot Bounce", "artist": "Chelsea Cope", "address": "100 block of North Spring Avenue", "type": "Sculpture", "photo_url": "http://i.imgur.com/pmxyi.jpg?1", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-11"}, "geometry": {"type": "Point", "coordinates": [-95.29944, 32.351434]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "children-of-peace", "title": "Children of Peace", "artist": "Gary Price", "description": "Cast Bronze", "address": "900 South Broadway Avenue", "type": "Sculpture", "photo_url": "http://i.imgur.com/rUikO.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-15"}, "geometry": {"type": "Point", "coordinates": [-95.300222, 32.339826]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "ross-bears", "title": "Ross\' Bears", "description": "Granite Sculpture", "address": "900 South Broadway Avenue", "type": "Sculpture", "photo_url": "http://i.imgur.com/SpJQI.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-15"}, "geometry": {"type": "Point", "coordinates": [-95.300034, 32.339776]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "butterfly-garden", "title": "Butterfly Garden", "description": "Cast Bronze", "type": "Sculpture", "photo_url": "http://i.imgur.com/0L8DF.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-15"}, "geometry": {"type": "Point", "coordinates": [-95.300219, 32.339559]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "goose-fountain", "title": "TJC Goose Fountain", "description": "Copper (?) Sculpture", "address": "1300 S. Mahon Ave.", "type": "Sculpture", "photo_url": "http://i.imgur.com/UWfS6.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-15"}, "geometry": {"type": "Point", "coordinates": [-95.28263, 32.333944]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "tjc-cement-totems", "description": "Cast Cement Totems", "address": "1300 S. Mahon Ave.", "type": "Sculpture", "photo_url": "http://i.imgur.com/lRmYd.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-15"}, "geometry": {"type": "Point", "coordinates": [-95.283894, 32.333899]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "alison", "title": "Alison", "description": "Cast Bronze", "address": "900 South Broadway Avenue", "type": "Sculpture", "photo_url": "http://i.imgur.com/7OcrG.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-15"}, "geometry": {"type": "Point", "coordinates": [-95.299887, 32.339809]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "jackson", "title": "Jackson", "description": "Cast Bronze", "address": "900 South Broadway Avenue", "type": "Sculpture", "photo_url": "http://i.imgur.com/aQJfv.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-15"}, "geometry": {"type": "Point", "coordinates": [-95.299939, 32.339828]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "505-third", "title": "Untitled", "description": "Stainless Steel", "address": "505 Third St.", "type": "Sculpture", "photo_url": "http://i.imgur.com/0moUY.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-15"}, "geometry": {"type": "Point", "coordinates": [-95.305429, 32.333082]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "obeidder", "title": "Obeidder Monster", "description": "Sharpie and Spray Paint", "address": "3319 Seaton St.", "type": "Street Art", "photo_url": "http://i.imgur.com/3aX7E.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "2012-04-15"}, "geometry": {"type": "Point", "coordinates": [-95.334619, 32.314431]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "sensor-device", "title": "Sensor Device", "artist": "Kurt Dyrhaug", "address": "University of Texas, Campus Drive", "type": "Sculpture", "photo_url": "http://media.hacktyler.com/artmap/photos/sensor-device.jpg", "photo_credit": "Photo by Christopher Groskopf. Used with permission.", "last_seen_date": "2012-04-16"}, "geometry": {"type": "Point", "coordinates": [-95.250699, 32.317216]}}',  # noqa: E501
        ])

    def test_ndgeojson_streaming(self):
        self.maxDiff = 5000
        self.assertLines(['--stream', '--no-inference', '--snifflimit', '0', '--lat', 'latitude', '--lon', 'longitude',
                          '--stream', 'examples/test_geo.csv'], [
            '{"type": "Feature", "properties": {"slug": "dcl", "title": "Downtown Coffee Lounge", "description": "In addition to being the only coffee shop in downtown Tyler, DCL also features regular exhibitions of work by local artists.", "address": "200 West Erwin Street", "type": "Gallery", "last_seen_date": "3/30/12"}, "geometry": {"type": "Point", "coordinates": [-95.30181, 32.35066]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "tyler-museum", "title": "Tyler Museum of Art", "description": "The Tyler Museum of Art on the campus of Tyler Junior College is the largest art museum in Tyler. Visit them online at <a href=\\"http://www.tylermuseum.org/\\">http://www.tylermuseum.org/</a>.", "address": "1300 South Mahon Avenue", "type": "Museum", "last_seen_date": "4/2/12"}, "geometry": {"type": "Point", "coordinates": [-95.28174, 32.33396]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "genecov", "title": "Genecov Sculpture", "description": "Stainless Steel Sculpture", "address": "1350 Dominion Plaza", "type": "Sculpture", "photo_url": "http://i.imgur.com/DICdn.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/4/12"}, "geometry": {"type": "Point", "coordinates": [-95.31571447849274, 32.299076986939205]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "gallery-main-street", "title": "Gallery Main Street", "description": "The only dedicated art gallery in Tyler. Visit them online at <a href=\\"http://www.heartoftyler.com/downtowntylerarts/\\">http://www.heartoftyler.com/downtowntylerarts/</a>.", "address": "110 West Erwin Street", "type": "Gallery", "last_seen_date": "4/9/12"}, "geometry": {"type": "Point", "coordinates": [-95.30123, 32.35066]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "spirit-of-progress", "title": "The Spirit of Progress", "address": "100 block of North Spring Avenue", "type": "Relief", "photo_url": "http://media.hacktyler.com/artmap/photos/spirit-of-progress.jpg", "photo_credit": "Photo by Christopher Groskopf. Used with permission.", "last_seen_date": "4/11/12"}, "geometry": {"type": "Point", "coordinates": [-95.2995, 32.3513]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "celestial-conversations-2", "title": "Celestial Conversations #2", "artist": "Simon Saleh", "description": "Steel Sculpture", "address": "100 block of North Spring Avenue", "type": "Sculpture", "photo_url": "http://media.hacktyler.com/artmap/photos/celestial-conversations-2.jpg", "photo_credit": "Photo by Christopher Groskopf. Used with permission.", "last_seen_date": "4/11/12"}, "geometry": {"type": "Point", "coordinates": [-95.2995, 32.351]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "pivot-bounce", "title": "Pivot Bounce", "artist": "Chelsea Cope", "address": "100 block of North Spring Avenue", "type": "Sculpture", "photo_url": "http://i.imgur.com/pmxyi.jpg?1", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/11/12"}, "geometry": {"type": "Point", "coordinates": [-95.29944, 32.351434]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "children-of-peace", "title": "Children of Peace", "artist": "Gary Price", "description": "Cast Bronze", "address": "900 South Broadway Avenue", "type": "Sculpture", "photo_url": "http://i.imgur.com/rUikO.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/15/12"}, "geometry": {"type": "Point", "coordinates": [-95.300222, 32.339826]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "ross-bears", "title": "Ross\' Bears", "description": "Granite Sculpture", "address": "900 South Broadway Avenue", "type": "Sculpture", "photo_url": "http://i.imgur.com/SpJQI.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/15/12"}, "geometry": {"type": "Point", "coordinates": [-95.300034, 32.339776]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "butterfly-garden", "title": "Butterfly Garden", "description": "Cast Bronze", "type": "Sculpture", "photo_url": "http://i.imgur.com/0L8DF.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/15/12"}, "geometry": {"type": "Point", "coordinates": [-95.300219, 32.339559]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "goose-fountain", "title": "TJC Goose Fountain", "description": "Copper (?) Sculpture", "address": "1300 S. Mahon Ave.", "type": "Sculpture", "photo_url": "http://i.imgur.com/UWfS6.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/15/12"}, "geometry": {"type": "Point", "coordinates": [-95.28263, 32.333944]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "tjc-cement-totems", "description": "Cast Cement Totems", "address": "1300 S. Mahon Ave.", "type": "Sculpture", "photo_url": "http://i.imgur.com/lRmYd.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/15/12"}, "geometry": {"type": "Point", "coordinates": [-95.283894, 32.333899]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "alison", "title": "Alison", "description": "Cast Bronze", "address": "900 South Broadway Avenue", "type": "Sculpture", "photo_url": "http://i.imgur.com/7OcrG.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/15/12"}, "geometry": {"type": "Point", "coordinates": [-95.299887, 32.339809]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "jackson", "title": "Jackson", "description": "Cast Bronze", "address": "900 South Broadway Avenue", "type": "Sculpture", "photo_url": "http://i.imgur.com/aQJfv.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/15/12"}, "geometry": {"type": "Point", "coordinates": [-95.299939, 32.339828]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "505-third", "title": "Untitled", "description": "Stainless Steel", "address": "505 Third St.", "type": "Sculpture", "photo_url": "http://i.imgur.com/0moUY.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/15/12"}, "geometry": {"type": "Point", "coordinates": [-95.305429, 32.333082]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "obeidder", "title": "Obeidder Monster", "description": "Sharpie and Spray Paint", "address": "3319 Seaton St.", "type": "Street Art", "photo_url": "http://i.imgur.com/3aX7E.jpg", "photo_credit": "Photo by Justin Edwards. Used with permission.", "last_seen_date": "4/15/12"}, "geometry": {"type": "Point", "coordinates": [-95.334619, 32.314431]}}',  # noqa: E501
            '{"type": "Feature", "properties": {"slug": "sensor-device", "title": "Sensor Device", "artist": "Kurt Dyrhaug", "address": "University of Texas, Campus Drive", "type": "Sculpture", "photo_url": "http://media.hacktyler.com/artmap/photos/sensor-device.jpg", "photo_credit": "Photo by Christopher Groskopf. Used with permission.", "last_seen_date": "4/16/12"}, "geometry": {"type": "Point", "coordinates": [-95.250699, 32.317216]}}',  # noqa: E501
        ])

    def test_rebuild(self):
        js = json.loads(self.get_output(['--rebuild', 'examples/rebuild_orders_schema.json',
                                         'examples/rebuild_orders.csv']))
        self.assertEqual(js, [
            {
                # Rows are out of order: O-102 appears first in the input.
                'order_id': 'O-102', 'customer': 'Ana', 'placed': '2024-05-01',
                'items': [
                    # Items are sorted by the declared order_by, not by row order.
                    {'line_no': 1.0, 'sku': 'SKU-7', 'qty': 3.0, 'discounts': [
                        {'code': 'WELCOME', 'amount': 2.5},
                        # A null value is not a missing child.
                        {'code': 'FLASH', 'amount': None},
                    ]},
                    {'line_no': 2.0, 'sku': 'SKU-8', 'qty': 1.0, 'discounts': []},
                ],
            },
            {
                'order_id': 'O-101', 'customer': 'Ben', 'placed': '2024-05-02',
                'items': [
                    # Join fan-out: repeated rows merge into one object per key.
                    {'line_no': 1.0, 'sku': 'SKU-1', 'qty': 2.0, 'discounts': [
                        {'code': 'SAVE10', 'amount': 5.0},
                    ]},
                    {'line_no': 2.0, 'sku': 'SKU-2', 'qty': 1.0, 'discounts': []},
                ],
            },
            {
                # A missing child (all of its columns are null) is an empty array.
                'order_id': 'O-103', 'customer': 'Cid', 'placed': '2024-05-03', 'items': [],
            },
        ])

    def test_rebuild_streaming_no_inference(self):
        lines = self.get_output_as_list(['--rebuild', 'examples/rebuild_orders_schema.json', '--stream',
                                         '--no-inference', '--snifflimit', '0', 'examples/rebuild_orders.csv'])
        self.assertEqual(len(lines), 4)  # 3 objects + trailing newline
        js = [json.loads(line) for line in lines[:3]]
        self.assertEqual([obj['order_id'] for obj in js], ['O-102', 'O-101', 'O-103'])
        self.assertEqual(js[0]['items'][0]['qty'], '3')
        self.assertIsNone(js[0]['items'][0]['discounts'][1]['amount'])
        self.assertEqual(js[2]['items'], [])

    def test_rebuild_indent(self):
        output = self.get_output(['--rebuild', 'examples/rebuild_conflict_schema.json',
                                  '-i', '2', 'examples/rebuild_conflict.csv'])
        js = json.loads(output)
        self.assertEqual(len(js), 1)
        self.assertTrue(output.startswith('[\n  {\n    "order_id": "O-201"'))
        self.assertTrue(output.endswith('\n]'))

    def test_rebuild_spill_matches_memory(self):
        args = ['--rebuild', 'examples/rebuild_orders_schema.json', 'examples/rebuild_orders.csv']
        self.assertEqual(self.get_output(args), self.get_output(args + ['--spill']))

    def test_rebuild_conflicts(self):
        output, error = self.get_output_and_error(['--rebuild', 'examples/rebuild_conflict_schema.json',
                                                   'examples/rebuild_conflict.csv'])
        js = json.loads(output)
        # The first-seen value wins.
        self.assertEqual(js, [
            {'order_id': 'O-201', 'customer': 'Grace', 'items': [
                {'line_no': 1.0, 'sku': 'SKU-1', 'qty': 1.0},
                {'line_no': 2.0, 'sku': 'SKU-9', 'qty': 5.0},
            ]},
        ])
        # Conflicts report the field, both values and both source rows.
        self.assertIn(
            'csvjson: conflict at $[order_id="O-201"].items[line_no=1.0].qty: kept 1.0 (row 1), ignored 2.0 (row 2)',
            error,
        )
        self.assertIn(
            'csvjson: conflict at $[order_id="O-201"].customer: kept "Grace" (row 1), ignored "G." (row 3)',
            error,
        )
        self.assertIn('csvjson: warning: 2 scalar conflict(s) detected', error)

    def test_rebuild_orphans(self):
        output, error = self.get_output_and_error(['--rebuild', 'examples/rebuild_orders_schema.json',
                                                   'examples/rebuild_orphans.csv'])
        js = json.loads(output)
        self.assertEqual(js, [
            {'order_id': 'O-401', 'customer': 'Lea', 'placed': '2024-06-01', 'items': [
                {'line_no': 2.0, 'sku': 'SKU-2', 'qty': 2.0, 'discounts': []},
            ]},
        ])
        self.assertIn('csvjson: warning: row 1: ignoring items.discounts values: items is missing', error)
        self.assertIn('csvjson: warning: row 2 skipped: all root key columns are null', error)

    def test_rebuild_composite_key_and_column_map(self):
        js = json.loads(self.get_output(['--rebuild', 'examples/rebuild_composite_schema.json',
                                         'examples/rebuild_composite.csv']))
        self.assertEqual(js, [
            {'order_id': 'A-1', 'region': 'EU', 'customer': 'Ada', 'note': 'fragile', 'lines': [
                {'line_no': 1.0, 'sku': 'X', 'price': 10.0},
                {'line_no': 2.0, 'sku': 'Y', 'price': 20.0},
            ]},
            {'order_id': 'A-1', 'region': 'US', 'customer': 'Ada', 'note': 'gift', 'lines': [
                {'line_no': 1.0, 'sku': 'Z', 'price': 30.0},
            ]},
        ])

    def test_rebuild_empty(self):
        self.assertEqual(
            self.get_output(['--rebuild', 'examples/rebuild_conflict_schema.json', 'examples/rebuild_empty.csv']),
            '[]',
        )

    def test_rebuild_options(self):
        self.assertError(
            launch_new_instance,
            ['--rebuild', 'examples/rebuild_orders_schema.json', '--key', 'order_id'],
            '--key is not allowed with --rebuild. The root key is declared in the schema.',
        )
        self.assertError(
            launch_new_instance,
            ['--rebuild', 'examples/rebuild_orders_schema.json', '--lat', 'latitude', '--lon', 'longitude'],
            '--lat and --lon are not allowed with --rebuild.',
        )
        self.assertError(
            launch_new_instance,
            ['--spill'],
            '--spill is only allowed with --rebuild.',
        )
        self.assertError(
            launch_new_instance,
            ['--rebuild', 'examples/does_not_exist.json'],
            'Unable to read the rebuild schema "examples/does_not_exist.json": No such file or directory',
        )
        self.assertError(
            launch_new_instance,
            ['--rebuild', 'examples/rebuild_bad_schema.json'],
            'The key field "nonexistent" of the root level does not match any column. Available fields: a, b, c.',
        )

    def test_rebuild_invalid_schema_json(self):
        f = io.StringIO()
        with redirect_stderr(f):
            with patch.object(sys, 'argv', ['csvjson', '--rebuild', 'examples/rebuild_invalid_schema.json',
                                            'examples/dummy.csv']):
                with self.assertRaises(SystemExit) as e:
                    launch_new_instance()
        self.assertEqual(e.exception.code, 2)
        self.assertIn('is not valid JSON', f.getvalue())
