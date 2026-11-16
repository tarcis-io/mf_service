# mf_service

The **MassFlow service** — a thin [FastAPI](https://fastapi.tiangolo.com/)
layer that exposes the [MassFlow engine](https://github.com/tarcis-io/mf_engine)
(`mf_engine`) over HTTP.

The engine is the core: `mf_service` depends on `mf_engine` and serves it. The
service stays a thin, well-structured HTTP boundary so endpoints can be added
over time without reshaping the project.

## Install

From the package directory (editable install for development):

```bash
pip install -e .
```

With the developer tooling (pytest / ruff / mypy / httpx):

```bash
pip install -e ".[dev]"
```

`mf_engine` is pinned to a specific `main` commit in `pyproject.toml`, so builds
are reproducible; bump the pin to adopt a newer engine.

## Run

```bash
mf-service                      # the installed console script
python -m mf_service            # equivalently, as a module
uvicorn mf_service:app --reload # or drive uvicorn directly, with auto-reload
```

The server binds to `MF_SERVICE_HOST`:`MF_SERVICE_PORT` (default
`0.0.0.0:8000`). Interactive API docs are served at `/docs` and the OpenAPI
schema at `/openapi.json`.

## Deploy (Google Cloud Run)

The `Procfile` defines the web process Cloud Run runs when deployed from source
with [Google Cloud buildpacks](https://cloud.google.com/docs/buildpacks/overview):

```
web: uvicorn mf_service:app --host 0.0.0.0 --port ${PORT:-8000}
```

Cloud Run injects a `PORT` environment variable and expects the container to
listen on it at `0.0.0.0`; the process binds `$PORT` (falling back to `8000`
locally). Note this is a different variable from `MF_SERVICE_PORT` — the
Procfile invokes `uvicorn` directly rather than the `mf-service` console script
precisely so it honours Cloud Run's `PORT`. Deploy with:

```bash
gcloud run deploy mf-service --source .
```

On Cloud Run, Firestore's project and credentials are auto-detected from the
service's identity, so `MF_SERVICE_FIRESTORE_PROJECT` is normally left unset
(see Configuration below).

## Endpoints

### `GET /health`

A liveness probe for load balancers and orchestrators. Returns:

```json
{ "status": "ok" }
```

### `GET /version`

The service identity and the engine version it is running against:

```json
{
  "name": "mf_service",
  "description": "MassFlow service: a FastAPI service exposing the MassFlow engine over HTTP.",
  "version": "0.1.0",
  "engine_version": "0.1.0"
}
```

`name`, `description`, and `version` are the service's own package metadata
(`pyproject.toml` is the single source of truth). `engine_version` is read from
`mf_engine.__version__` — the engine's own single source of truth — so the two
projects never disagree about which engine build is deployed.

Note: in an uninstalled source tree the metadata is unavailable, so `version`
and `engine_version` each fall back to `"0.0.0"`; run from an installed package
(`pip install -e .`) to see the real versions.

### Batches

CRUD over a **batch** — a unit of processing work owned by a user. Every batch
route requires an `X-User-Id` request header (this service is called by another
service, which identifies the end user that way); a request without it gets
`422`. All operations are scoped to that user: a batch id belonging to another
user reads as `404`.

A batch has a server-assigned `id` (UUID) and `created` timestamp, a `name`, a
`status`, and an `error_message` (set only when `status` is `error`). The status
moves through two phases — first the file **upload** phase, then the engine
**processing** phase:

```
creating → created → processing → processed
       └───────┴──────────────┴──────────────→ error
```

A new batch is born `creating` — creation and the upload of its files are a
single action — and becomes `created` once every file is in storage (see
below). The `processing` → `processed` steps happen later, driven by the engine
out-of-band, not through this API. `error` is the shared failure state for
either phase.

| Method & path         | Body                 | Description                                                    |
| --------------------- | -------------------- | -------------------------------------------------------------- |
| `POST /batches`       | multipart (see below)| Create a batch + upload its files. `409` if the name is taken. |
| `GET /batches`        | —                    | List the caller's batches, newest first.                       |
| `GET /batches/{id}`   | —                    | Fetch one batch. `404` if absent.                              |
| `PATCH /batches/{id}` | `{ "name" }`         | Rename. `404` if absent, `409` on dup name.                    |
| `DELETE /batches/{id}`| —                    | Delete. `404` if absent.                                       |

**Creating a batch (file upload).** `POST /batches` is `multipart/form-data`: a
`name` field plus five **required** files (a missing one is `422`):

| Form field                      | Uploaded as                         |
| ------------------------------- | ----------------------------------- |
| `mzmine_annotations`            | `mzmine_annotations.csv`            |
| `mzmine_full_feature_table`     | `mzmine_full_feature_table.csv`     |
| `mzmine_metadata`               | `mzmine_metadata.csv`               |
| `gnps_feature_table_reformated` | `gnps_feature_table_reformated.csv` |
| `gnps_merged_results`           | `gnps_merged_results.tsv`           |

The response returns promptly (status `creating`) with the new batch; the files
are then uploaded to Cloud Storage by a **background task** under the key
`<batch_id>/<filename>` in the configured bucket. The batch becomes `created`
once all files are stored, or `error` (with `error_message`) if any upload
fails. Poll `GET /batches/{id}` to observe it.

```bash
curl -X POST http://localhost:8000/batches \
  -H "X-User-Id: user-123" \
  -F "name=Spring run" \
  -F "mzmine_annotations=@mzmine_annotations.csv" \
  -F "mzmine_full_feature_table=@mzmine_full_feature_table.csv" \
  -F "mzmine_metadata=@mzmine_metadata.csv" \
  -F "gnps_feature_table_reformated=@gnps_feature_table_reformated.csv" \
  -F "gnps_merged_results=@gnps_merged_results.tsv"
```

**Name uniqueness.** A name must be unique *per user* (two different users may
use the same name). Only the name is mutable — all other fields are
server-managed. Because Firestore has no unique constraints, uniqueness is
enforced with a transaction that reserves the name via a guard document, so two
concurrent creates of the same name cannot both succeed (see
`mf_service.firestore_repository`).

```json
// 201 response to the POST /batches above
{
  "id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
  "user_id": "user-123",
  "name": "Spring run",
  "created": "2026-01-15T09:30:00Z",
  "status": "creating",
  "error_message": null
}
```

## Configuration

Configuration is centralised in `mf_service.config.Settings` (a
pydantic-settings model). Fields are overridable via an environment variable
with the `MF_SERVICE_` prefix, or a `.env` file — no code reads the environment
directly. See `.env.example` for the available variables.

| Variable                             | Default         | Description                                         |
| ------------------------------------ | --------------- | --------------------------------------------------- |
| `MF_SERVICE_HOST`                    | `0.0.0.0`       | Bind address for the server.                        |
| `MF_SERVICE_PORT`                    | `8000`          | Bind port for the server.                           |
| `MF_SERVICE_NAME`                    | package name    | Overrides the reported name.                        |
| `MF_SERVICE_DESCRIPTION`             | package summary | Overrides the reported description.                 |
| `MF_SERVICE_FIRESTORE_PROJECT`       | auto-detected   | GCP project for Firestore; blank = auto-detect.     |
| `MF_SERVICE_BATCHES_COLLECTION`      | `batches`       | Firestore collection for batch documents.           |
| `MF_SERVICE_BATCH_NAMES_COLLECTION`  | `batch_names`   | Firestore collection for the name-uniqueness guard. |
| `MF_SERVICE_STORAGE_BUCKET`          | _(required)_    | Cloud Storage bucket for batch input files.         |

The reported `version` is **not** configurable: it is always read from the
package metadata so it reflects the build that is actually deployed. Setting
`MF_SERVICE_VERSION` has no effect.

Firestore and Cloud Storage both use Application Default Credentials: on Cloud
Run the project and credentials are auto-detected, so
`MF_SERVICE_FIRESTORE_PROJECT` is usually left unset (the storage client shares
it). Locally, point `GOOGLE_APPLICATION_CREDENTIALS` at a service-account key
(or run the Firestore emulator and set `FIRESTORE_EMULATOR_HOST`).
`MF_SERVICE_STORAGE_BUCKET` has no default and must be set for uploads to
succeed; the bucket must already exist and the service identity must have write
access to it.

## Version

`mf_service.__version__` is read from the installed package metadata (the single
source of truth is `pyproject.toml`). When the package is imported from a source
tree that has **not** been installed, the metadata is unavailable and the value
falls back to `"0.0.0"` — matching `mf_engine`'s behaviour.

## Development

Install the dev tooling and run the checks:

```bash
pip install -e ".[dev]"
pytest            # tests (with coverage; see below)
ruff check .      # lint
mypy              # type-check (config in pyproject.toml drives what's checked)
```

`pytest` runs with coverage by default (configured in `pyproject.toml`): it
prints the missing lines and fails if coverage drops below the gate, so new
untested code is caught in CI.

Or run all three at once with the bundled `Makefile`:

```bash
make check        # lint + type-check + tests
```

## Layout

```
mf_service/
├── pyproject.toml                   # repository root
├── README.md
├── LICENSE
├── Makefile
├── .env.example
├── src/
│   └── mf_service/                  # the importable package
│       ├── __init__.py              # exposes `app`, `create_app`, `__version__`
│       ├── __main__.py              # console entry point (uvicorn)
│       ├── app.py                   # the FastAPI application factory
│       ├── config.py                # Settings (pydantic-settings)
│       ├── domain.py                # storage-agnostic Batch entity + status enum
│       ├── repository.py            # BatchRepository contract + in-memory impl
│       ├── firestore_repository.py  # Firestore-backed BatchRepository
│       ├── storage.py               # StorageGateway contract + GCS/in-memory impls
│       ├── uploads.py               # required file set + background upload task
│       ├── deps.py                  # Depends seam (engine, repository, storage, X-User-Id)
│       ├── schemas.py               # request/response models (API contracts)
│       ├── py.typed                 # PEP 561 typing marker
│       └── api/
│           ├── __init__.py          # the API package (grouped by version)
│           └── v1/
│               ├── __init__.py      # aggregates the v1 routes into one router
│               ├── health.py        # GET /health
│               ├── version.py       # GET /version
│               └── batches.py       # CRUD /batches
└── tests/
    ├── conftest.py                  # shared fixtures (TestClient, in-memory repo)
    ├── test_package.py              # package surface + app factory
    ├── test_main.py                 # console entry point (uvicorn wiring)
    ├── test_config.py               # Settings + env overrides + cache
    ├── test_deps.py                 # engine + repository dependency seams
    ├── test_health.py               # /health endpoint
    ├── test_version.py              # /version endpoint
    ├── test_repository.py           # in-memory BatchRepository
    ├── test_firestore_repository.py # Firestore repo (fake client)
    ├── test_storage.py              # StorageGateway (in-memory + GCS seam)
    ├── test_uploads.py              # background upload task (status + paths)
    └── test_batches.py              # /batches CRUD + multipart upload endpoint
```

This uses the **src layout** so tests always run against the installed package,
not a stray copy on the path — matching `mf_engine`. Routes are grouped under
`api/v1`, so a second API version (or a new endpoint) slots in without
disturbing the existing ones.

## Adding engine-backed endpoints

Beyond the `/health` and `/version` identity endpoints and the `/batches` CRUD
(which uploads inputs to storage), the next step is routes that call the engine
to process an uploaded batch. The seam for that is
`mf_service.deps`: it exposes a `Depends` provider per engine processor (e.g.
`get_gnps_processor` / the `GNPSProcessorDep` alias), so a route declares the
processor it needs and the wiring — construction, lifetime, lazy import of
`mf_engine` — stays in one place. Tests swap a processor via
`app.dependency_overrides`, the same mechanism `create_app` uses for settings.

Keep engine-backed routes **synchronous** (`def`, not `async def`): the engine
does CPU-bound pandas work, which FastAPI runs in a worker threadpool. An
`async def` route must only ever `await` real I/O — blocking work inside one
stalls the event loop for every request. Cross-cutting concerns (CORS,
exception handlers mapping engine errors to HTTP, request-id/timing middleware)
are registered in `create_app`, which marks the spot.
