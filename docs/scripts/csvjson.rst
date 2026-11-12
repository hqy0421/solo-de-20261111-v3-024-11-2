=======
csvjson
=======

Description
===========

Converts a CSV file into JSON or GeoJSON (depending on flags):

.. code-block:: none

   usage: csvjson [-h] [-d DELIMITER] [-t] [-q QUOTECHAR] [-u {0,1,2,3}] [-b]
                  [-p ESCAPECHAR] [-z FIELD_SIZE_LIMIT] [-e ENCODING] [-L LOCALE]
                  [-S] [--blanks] [--null-value NULL_VALUES [NULL_VALUES ...]]
                  [--date-format DATE_FORMAT] [--datetime-format DATETIME_FORMAT]
                  [-H] [-K SKIP_LINES] [-v] [-l] [--zero] [-V] [-i INDENT]
                  [-k KEY] [--lat LAT] [--lon LON] [--type TYPE]
                  [--geometry GEOMETRY] [--crs CRS] [--no-bbox] [--stream]
                  [--rebuild SCHEMA] [--spill] [-y SNIFF_LIMIT] [-I]
                  [FILE]

   Convert a CSV file into JSON (or GeoJSON).

   positional arguments:
     FILE                  The CSV file to operate on. If omitted, will accept
                           input as piped data via STDIN.

   optional arguments:
     -h, --help            show this help message and exit
     -i INDENT, --indent INDENT
                           Indent the output JSON this many spaces. Disabled by
                           default.
     -k KEY, --key KEY     Output JSON as an object keyed by a given column, KEY,
                           rather than as an array. All column values must be
                           unique. If --lat and --lon are specified, this column
                           is used as the GeoJSON Feature ID.
     --lat LAT             A column index or name containing a latitude. Output
                           will be GeoJSON instead of JSON. Requires --lon.
     --lon LON             A column index or name containing a longitude. Output
                           will be GeoJSON instead of JSON. Requires --lat.
     --type TYPE           A column index or name containing a GeoJSON type.
                           Output will be GeoJSON instead of JSON. Requires --lat
                           and --lon.
     --geometry GEOMETRY   A column index or name containing a GeoJSON geometry.
                           Output will be GeoJSON instead of JSON. Requires --lat
                           and --lon.
     --crs CRS             A coordinate reference system string to be included
                           with GeoJSON output. Requires --lat and --lon.
     --no-bbox             Disable the calculation of a bounding box.
     --stream              Output JSON as a stream of newline-separated objects,
                           rather than an as an array.
     --rebuild SCHEMA      Rebuild nested JSON from a denormalized CSV (such as
                           a join result), using a JSON schema file that declares
                           the parent-child paths and the composite key of each
                           level. Rows are merged by business identity instead
                           of producing one object per row.
     --spill               With --rebuild: keep the object index in a temporary
                           on-disk database instead of in memory, for large
                           inputs. The temporary directory is chosen via TMPDIR.
     -y SNIFF_LIMIT, --snifflimit SNIFF_LIMIT
                           Limit CSV dialect sniffing to the specified number of
                           bytes. Specify "0" to disable sniffing entirely, or
                           "-1" to sniff the entire file.
     -I, --no-inference    Disable type inference (and --locale, --date-format,
                           --datetime-format, --no-leading-zeroes) when parsing
                           the input.

See also: :doc:`../common_arguments`.

Rebuilding nested JSON
======================

With :code:`--rebuild SCHEMA`, a denormalized CSV — typically the result of a
join, where the parent row is repeated for every child — is folded back into
nested JSON. Instead of one flat object per row, rows are merged by business
identity: each level of the output is identified by a (composite) key declared
in the schema, so repeated rows complete a single object instead of
duplicating it, and join fan-out does not produce duplicate children.

The schema is a JSON file declaring the parent-child paths and the key of each
level:

.. code-block:: json

   {
     "key": "order_id",
     "children": {
       "items": {
         "key": "line_no",
         "order_by": "line_no",
         "children": {
           "discounts": {
             "key": "code"
           }
         }
       }
     }
   }

- ``key`` (required at every level): a field name, or a list of field names
  for a composite key. Rows whose key is equal at a level describe the same
  object.
- ``children``: child arrays. The name becomes an array property of the
  parent object; it is always emitted, ``[]`` when the child is missing.
- ``order_by`` (optional): a field or list of fields used to sort the array.
  Nulls sort last. Without ``order_by``, objects appear in first-seen order,
  so out-of-order input is fine.
- ``columns`` (optional): a map of CSV column names (or, with ``--zero``,
  0-based indices) to output field names, for CSVs whose columns are not
  dotted paths.

CSV columns are assigned to levels by dotted prefix: ``items.discounts.code``
belongs to the ``items.discounts`` path, and columns without a declared prefix
belong to the root. ``key`` and ``order_by`` refer to output field names
(after any ``columns`` renaming).

Merge semantics:

- A child is *present* in a row when at least one of its columns is non-null.
  A row whose child columns are all null means the child is missing (a LEFT
  JOIN miss) — it contributes no array element, which is distinct from a
  child whose values are null.
- When several rows identify the same object, a null scalar never conflicts:
  a later non-null value completes it. Two different non-null values for the
  same scalar field are a conflict: the first-seen value is kept, and every
  conflict is reported to standard error with the field, both values and
  their 1-based data row numbers. After 1000 conflicts only the total count
  is reported.
- Rows with an all-null root key, and data for a grandchild whose parent is
  missing, are skipped with a warning.

For large inputs, ``--spill`` keeps the object index in a temporary on-disk
database (located via ``TMPDIR``) instead of in memory. Combine it with
``--no-inference --snifflimit 0`` to also stream the input instead of loading
it, and with ``--stream`` to emit newline-delimited root objects.

Examples
========

Convert veteran's education dataset to JSON keyed by state abbreviation:

.. code-block:: console

   $ csvjson -k "State Abbreviate" -i 4 examples/realdata/FY09_EDU_Recipients_by_State.csv
   {
       "AL": {
           "State Name": "ALABAMA",
           "State Abbreviate": "AL",
           "Code": 1.0,
           "Montgomery GI Bill-Active Duty": 6718.0,
           "Montgomery GI Bill- Selective Reserve": 1728.0,
           "Dependents' Educational Assistance": 2703.0,
           "Reserve Educational Assistance Program": 1269.0,
           "Post-Vietnam Era Veteran's Educational Assistance Program": 8.0,
           "TOTAL": 12426.0,
           "j": null
       },
       "...": {
           "...": "..."
       }
   }

Convert locations of public art into GeoJSON:

.. code-block:: console

   $ csvjson --lat latitude --lon longitude --k slug --crs EPSG:4269 -i 4 examples/test_geo.csv
   {
       "type": "FeatureCollection", 
       "bbox": [
           -95.334619, 
           32.299076986939205, 
           -95.250699, 
           32.351434
       ], 
       "crs": {
           "type": "name", 
           "properties": {
               "name": "EPSG:4269"
           }
       }, 
       "features": [
           {
               "type": "Feature", 
               "id": "dcl", 
               "geometry": {
                   "type": "Point", 
                   "coordinates": [
                       -95.30181, 
                       32.35066
                   ]
               }, 
               "properties": {
                   "title": "Downtown Coffee Lounge", 
                   "artist": null, 
                   "description": "In addition to being the only coffee shop in downtown Tyler, DCL also features regular exhibitions of work by local artists.", 
                   "install_date": null, 
                   "address": "200 West Erwin Street", 
                   "type": "Gallery", 
                   "photo_url": null, 
                   "photo_credit": null, 
                   "last_seen_date": "2012-03-30"
               }
           }, 
           {
               "...": "..."
           }
       ], 
       "crs": {
           "type": "name",
           "properties": {
               "name": "EPSG:4269"
           }
       }
   }

Rebuild nested JSON from the result of a join between orders, line items and
discounts (see `Rebuilding nested JSON`_ for the schema):

.. code-block:: console

   $ csvjson --rebuild examples/rebuild_orders_schema.json -i 2 examples/rebuild_orders.csv
   [
     {
       "order_id": "O-102",
       "customer": "Ana",
       "placed": "2024-05-01",
       "items": [
         {
           "line_no": 1.0,
           "sku": "SKU-7",
           "qty": 3.0,
           "discounts": [
             {
               "code": "WELCOME",
               "amount": 2.5
             },
             {
               "code": "FLASH",
               "amount": null
             }
           ]
         },
         {
           "...": "..."
         }
       ]
     },
     {
       "...": "..."
     }
   ]
