# Provider profiles

PostgreSQL accepts plain SQL or recognized custom/tar dump signatures; MySQL/MariaDB accepts SQL
with recognizable dump structure; SQLite requires a valid database header; MongoDB accepts a
bounded archive or dump directory; object storage accepts a dedicated local export directory or
manifest. Extensions alone are weak evidence.

Logical exports from RDS, Cloud SQL, Azure Database, or similar services map to their database
engine. Provider-native snapshot IDs are not local recovery inputs.
