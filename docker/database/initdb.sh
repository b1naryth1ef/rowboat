#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "postgres" -d rowboat -c "CREATE EXTENSION hstore;"
psql -v ON_ERROR_STOP=1 --username "postgres" -d rowboat -c "CREATE EXTENSION pg_trgm;"
