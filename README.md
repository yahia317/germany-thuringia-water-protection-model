# Thuringia Water Protection Data Pipeline

A Python and PostGIS pipeline for retrieving, validating, and loading water protection zone data from Thuringia, Germany.

## 1. Data Source

- **Authority:** Thuringian State Office for the Environment, Mining and Nature Conservation (TLUBN)
- **WFS endpoint:** `http://www.geoproxy.geoportal-th.de/geoproxy/services/wsg_hqsg_wfs`
- **Layer:** `tmlnu:WASSER_UND_HEILQUELLENSCHUTZGEBIETE`
- **Coordinate reference system:** EPSG:25832
- **Attribution:** © GDI-Th
- **Licence:** [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)

The source layer contains water protection zones (WSG) and mineral spring protection zones (HQSG). The pipeline targets WSG records.

## 2. Get the Code

Clone the repository and enter the project directory:

```bash
git clone https://github.com/yahia317/germany-thuringia-water-protection-model.git
cd germany-thuringia-water-protection-model
```

## 3. Requirements

- Docker Desktop with Docker Compose
- Internet access to download the Docker image dependencies and retrieve live WFS data

Python and the project dependencies are installed inside the application container. You do not need to install them on your host machine.

## 4. Prepare Docker and the Database

### Step 1: Configure the environment

Create your local environment file:

```powershell
Copy-Item .env.example .env
```

Set `POSTGRES_PASSWORD` in `.env` to a local development password. Do not commit `.env` or real credentials.

### Step 2: Build the application image

```bash
docker compose build app
```

This installs the Python dependencies listed in `requirements.txt` inside the Docker image.

### Step 3: Start PostGIS

```bash
docker compose up -d postgis
```

Wait until the database health check reports that PostGIS is healthy:

```bash
docker compose ps
```

### Step 4: Initialize the database schema

```powershell
Get-Content .\init.sql | docker compose exec -T postgis psql -U iris -d iris
```

This creates the PostGIS extension, staging schema, target table, and spatial index. Run it after the database is first created, or when setting up a fresh database.

## 5. Run the Pipeline and Tests

```powershell
docker compose run --rm app
```

The pipeline connects to the configured Web Feature Service (WFS), retrieves the source data, and processes it for loading into the staging database. An internet connection and an available WFS endpoint are required.

Running the pipeline also serves as an end-to-end check. Review the console output to verify that the following requirements are addressed:

- Service metadata is inspected, and water-protection zone geometries and legally described zone classes are retrieved for the specified state.
- Official zone codes, restriction categories, and extraction rates are mapped only where published; absent or unknown attributes are explicitly identified.
- Multipart polygons are validated, geometries are transformed from the declared source CRS to EPSG:25832, and parcel-level containment and intersection are tested.
- Data is loaded idempotently into `iris_staging.water_protection_zones`, keyed by source zone ID and state code.
- Data origin, update dates, empty or error responses, and coverage limitations are recorded.

Compare the output with these requirements and check for any reported errors or limitations. Successful execution alone does not guarantee that every requirement was satisfied.

## 6. Run mocked data Tests

### Run the mocked and non-integration tests

```bash
docker compose run --rm app python -m pytest -v -m "not integration"
```

These tests validate attribute mapping, geometry validation and repair, coordinate reference systems, parcel spatial checks, duplicate-key handling, and mocked WFS responses without requiring the integration test database.

### Prepare the integration test database

Create a dedicated test database:

```bash
docker compose exec postgis psql -U iris -d postgres -c "CREATE DATABASE iris_test;"
```

Initialize its schema:

```powershell
Get-Content .\init.sql | docker compose exec -T postgis psql -U iris -d iris_test
```

If `iris_test` already exists, do not create it again.

### Run the integration test

Replace `YOUR_PASSWORD` in the next command with the password value configured in `.env`.
```bash
docker compose run --rm -e IRIS_TEST_DATABASE_URL="postgresql://iris:YOUR_PASSWORD@postgis:5432/iris_test" app python -m pytest -v -m integration
```



The integration test uses the dedicated test database. Do not configure it to point to `iris_core` or any production database.

### Run the complete test suite

Also replace `YOUR_PASSWORD` in the next command with the password value configured in `.env`.
```bash
docker compose run --rm -e IRIS_TEST_DATABASE_URL="postgresql://iris:YOUR_PASSWORD@postgis:5432/iris_test" app python -m pytest -v
```

## 7. Project Structure

- `thuringia_water_protection_model.py` — data-processing and validation logic
- `test_.py` — pipeline execution script
- `tests/` — automated tests
- `init.sql` — PostGIS schema initialization
- `docker-compose.yml` — application and database services
- `Dockerfile` — Python application image
- `requirements.txt` — pinned Python dependencies
- `data/` — retrieval manifest and local data artifacts
- `Notes.md` — candidate notes and limitations

## 8. Data Handling and Limitations

Downloaded XML files are excluded from version control, while the retrieval manifest records the latest download attempt.

Invalid geometries are identified and repaired during processing; the original downloaded source files are not modified in place. Restriction categories and extraction rates are not inferred when they are unavailable in the inspected source attributes.

Live retrieval depends on the external WFS service, its availability, access permissions, and published schema. Results may change as the source data is updated.