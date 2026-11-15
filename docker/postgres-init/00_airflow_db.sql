-- Airflow metadata database: same Postgres server, separate database and role
-- (in production it would be a managed instance of its own).
CREATE USER airflow WITH PASSWORD 'airflow';
CREATE DATABASE airflow OWNER airflow;
