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

## Endpoints

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
falls back to `"0.0.0"`; run from an installed package (`pip install -e .`) to
see the real versions.

### Batches

CRUD over a **batch** — a unit of processing work owned by a user. Every batch
route requires an `X-User-Id` request header (this service is called by another
service, which identifies the end user that way); a request that omits it — or
sends it blank/whitespace-only — gets `422` (the value is trimmed, so a blank id
can never become a shared namespace). All operations are scoped to that user: a
batch id belonging to another user reads as `404`.

A batch has a server-assigned `id` (UUID4) and `created` timestamp, a `name`, a
`status`, an `error_message` (set only when `status` is `error`), and an
`updated` timestamp that tracks the last **status change** (it equals `created`
until the status first changes). The status moves through the lifecycle:

```
creating → created → processing → processed
       └───────┴──────────────┴──────────────→ error
```

A new batch is born `creating`. The later transitions are a convention, not an
enforced constraint. `name` must be unique **per user** (two different users may
use the same name, and uniqueness is case-sensitive); only `name` is mutable
through this API — all other fields are server-managed. Request bodies are
strict: an unknown field (including an attempt to set a server-managed one) is
rejected with `422`.

| Method & path                        | Body                 | Description                                                                                          |
| ------------------------------------ | -------------------- | ---------------------------------------------------------------------------------------------------- |
| `POST /batches`                      | `multipart/form-data`| Create a batch from its file uploads (see below). `409` if name taken, `413` if a file is too large. |
| `GET /batches`                       | —                    | List the caller's batches, newest first.                                                             |
| `GET /batches/{id}`                  | —                    | Fetch one batch. `404` if absent.                                                                    |
| `GET /batches/{id}/files`            | —                    | List the batch's stored files. `404` if the batch is absent.                                         |
| `GET /batches/{id}/files/{filename}` | —                    | Download one stored file. `404` if the batch or file is absent.                                      |
| `GET /batches/{id}/files/archive`    | —                    | Download all stored files as one zip. `404` if the batch is absent.                                  |
| `PATCH /batches/{id}`                | `{ "name" }`         | Rename. `404` if absent, `409` on duplicate name.                                                    |
| `DELETE /batches/{id}`               | —                    | Delete the batch and its stored files. `404` if absent.                                              |
| `POST /process`                      | `{ "batch_id"? }`    | Start processing a batch (see below). No `X-User-Id`. `404`/`409` per the rules below.               |
| `POST /process/timeouts`             | —                    | Time out batches stuck in-progress (see below). No `X-User-Id`. `?older_than_seconds=` optional.     |

### Creating a batch (file upload)

`POST /batches` is a `multipart/form-data` request with a `name` field and one
part per required file, each keyed by its filename and at most
`MF_SERVICE_MAX_UPLOAD_BYTES` (10 MiB by default — a larger file is rejected with
`413`). The five required files are:

```
mzmine_annotations.csv
mzmine_full_feature_table.csv
mzmine_metadata.csv
gnps_feature_table_reformated.csv
gnps_merged_results.tsv
```

A missing part is a `422`. The batch is persisted and returned immediately with
status `creating`; the files are then uploaded to Cloud Storage in a background
job that flips the batch to `created` once all five are stored, or to `error`
(with `error_message` set) if an upload fails. Both transitions update
`updated`. Poll `GET /batches/{id}` to observe the status move.

```bash
# Create a batch (one -F per file; the field name is the filename)
curl -X POST http://localhost:8000/batches \
  -H "X-User-Id: user-123" \
  -F "name=Spring run 2026" \
  -F "mzmine_annotations.csv=@mzmine_annotations.csv" \
  -F "mzmine_full_feature_table.csv=@mzmine_full_feature_table.csv" \
  -F "mzmine_metadata.csv=@mzmine_metadata.csv" \
  -F "gnps_feature_table_reformated.csv=@gnps_feature_table_reformated.csv" \
  -F "gnps_merged_results.tsv=@gnps_merged_results.tsv"
```

```json
// 201 response (status is 'creating'; the upload completes in the background)
{
  "id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
  "user_id": "user-123",
  "name": "Spring run 2026",
  "created": "2026-01-15T09:30:00Z",
  "updated": "2026-01-15T09:30:00Z",
  "status": "creating",
  "error_message": null
}
```

### Listing and downloading files

Once a batch's upload has completed, its files can be listed, fetched one at a
time, or downloaded together as a zip. All three routes require `X-User-Id` and
resolve the batch by `(user_id, id)` first, so a missing batch — or one owned by
another user — is a `404`.

`GET /batches/{id}/files` returns the files currently stored under the batch
(a batch still uploading, or one that errored, may list fewer than five):

```json
// 200 response
[
  {
    "filename": "mzmine_annotations.csv",
    "size": 20480,
    "content_type": "text/csv",
    "updated": "2026-01-15T09:30:05Z"
  }
]
```

`GET /batches/{id}/files/{filename}` returns that one file's bytes as
`application/octet-stream` with a `Content-Disposition: attachment` header; a
filename with no stored object is a `404`.

```bash
curl -OJ http://localhost:8000/batches/3f2504e0-.../files/mzmine_annotations.csv \
  -H "X-User-Id: user-123"
```

`GET /batches/{id}/files/archive` returns all of the batch's stored files as a
single `application/zip` (each entry named by its filename). It zips whatever is
present, so a partially-uploaded batch yields fewer entries and a batch with no
files yields a valid empty zip. The archive is built in memory.

```bash
curl -OJ http://localhost:8000/batches/3f2504e0-.../files/archive \
  -H "X-User-Id: user-123"
```

### Processing

`POST /process` starts processing a batch. It is a **general** operation, not
scoped to a user, so it takes **no** `X-User-Id`. The optional JSON body selects
the batch:

- with `{"batch_id": "..."}` — that batch is processed; `404` if it does not
  exist, `409` if it is not in the `created` status.
- with no body (or no `batch_id`) — the **oldest** batch in the `created` status
  is processed; `404` if none is waiting.

The selected batch is flipped to `processing` and returned immediately (same
shape as `GET /batches/{id}`). A background job then runs the processing
**pipeline** over the batch's uploaded files and, on success, moves it to
`processed`; if any step fails the batch is moved to `error` with the failure in
`error_message`. Both outcomes update `updated`. Poll `GET /batches/{id}` to
watch the status move.

The pipeline is an ordered list of tasks that will grow over time. Today it runs
one task: `mf_engine`'s GNPS processor over the batch's input tables, writing the
per-feature result to storage as `annotations.csv` (listed and downloaded
through the file endpoints above, alongside the inputs).

```bash
# Process a specific batch
curl -X POST http://localhost:8000/process \
  -H "Content-Type: application/json" \
  -d '{"batch_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301"}'

# Or process the oldest 'created' batch
curl -X POST http://localhost:8000/process
```

#### Timing out stuck batches

Processing and uploading run in in-process background jobs, so if an instance
dies mid-job a batch can be left wedged in an in-progress status (`creating` or
`processing`) with nothing to finish it. `POST /process/timeouts` is the sweep
that clears them: it moves every in-progress batch whose `updated` is older than
a threshold to `error` with a timeout message, and returns the batches it timed
out. Like `/process` it is a global maintenance operation (no `X-User-Id`), meant
to run on a schedule.

The threshold is `older_than_seconds` when given (a positive integer), otherwise
`MF_SERVICE_PROCESSING_TIMEOUT_SECONDS` (default one hour).

```bash
# Time out anything stuck longer than the configured default
curl -X POST http://localhost:8000/process/timeouts

# Or override the threshold (e.g. 30 minutes)
curl -X POST "http://localhost:8000/process/timeouts?older_than_seconds=1800"
```

## Persistence

Batches are stored in **Google Cloud Firestore**, one document per batch (keyed
by the batch UUID) in a single collection. The collection name and the Firestore
database are configurable (see [Configuration](#configuration)); the database
defaults to Firestore's built-in `(default)` database and the collection to
`batches`.

Storage sits behind the `mf_service.repository.BatchRepository` interface, so the
routes are unaware of the backend. `FirestoreBatchRepository` is the default; an
in-memory implementation backs the tests (injected via FastAPI's dependency
overrides), so the suite needs no GCP access.

Credentials and the project are resolved from **Application Default
Credentials** — on Cloud Run both are auto-detected from the service identity,
so `MF_SERVICE_FIRESTORE_PROJECT` is normally left unset. Locally, point
`GOOGLE_APPLICATION_CREDENTIALS` at a service-account key, or run the Firestore
emulator and set `FIRESTORE_EMULATOR_HOST`.

Per-user name uniqueness is checked inside a transaction (query-then-write).
Firestore has no unique constraint, so under two truly simultaneous creates of
the same name a duplicate could slip through; the cost of closing that last gap
(a second guard collection) was judged not worth it here. Listing a user's
batches newest-first uses a `where` + `order_by` query that needs a composite
index on `(user_id, created)`; Firestore's first-run error links straight to
creating it.

A batch's uploaded **files** live in **Google Cloud Storage**, one folder per
batch: `<bucket>/<batch id>/<filename>`. The bucket is configurable
(`MF_SERVICE_STORAGE_BUCKET`, default `batches`) and the GCP project is the same
one Firestore uses. This sits behind the `mf_service.storage.BatchStorage`
interface (`GCSBatchStorage` is the default; an in-memory implementation backs
the tests), so the routes don't know the backend. Deleting a batch removes its
whole folder; a failed upload leaves whatever was written in place until the
batch is deleted.

## Configuration

Configuration is centralised in `mf_service.core.config.Settings` (a
pydantic-settings model). Fields are overridable via an environment variable
with the `MF_SERVICE_` prefix, or a `.env` file — no code reads the environment
directly. See `.env.example` for the available variables.

| Variable                                | Default         | Description                                                   |
| --------------------------------------- | --------------- | ------------------------------------------------------------- |
| `MF_SERVICE_HOST`                       | `0.0.0.0`       | Bind address for the server.                                  |
| `MF_SERVICE_PORT`                       | `8000`          | Bind port for the server.                                     |
| `MF_SERVICE_NAME`                       | package name    | Overrides the reported name.                                  |
| `MF_SERVICE_DESCRIPTION`                | package summary | Overrides the reported description.                           |
| `MF_SERVICE_FIRESTORE_PROJECT`          | auto-detected   | GCP project (Firestore + Storage); blank = auto-detect (ADC). |
| `MF_SERVICE_FIRESTORE_DATABASE`         | `(default)`     | Firestore database to use; set a name for a named one.        |
| `MF_SERVICE_BATCHES_COLLECTION`         | `batches`       | Firestore collection batches are stored in.                   |
| `MF_SERVICE_STORAGE_BUCKET`             | `batches`       | Cloud Storage bucket for uploaded batch files.                |
| `MF_SERVICE_MAX_UPLOAD_BYTES`           | `10485760`      | Max bytes per uploaded file (10 MiB); larger → `413`.         |
| `MF_SERVICE_PROCESSING_TIMEOUT_SECONDS` | `3600`          | Age (s) after which a stuck in-progress batch is timed out.   |

The reported `version` is **not** configurable: it is always read from the
package metadata so it reflects the build that is actually deployed. Setting
`MF_SERVICE_VERSION` has no effect.

## Version

`mf_service.__version__` is read from the installed package metadata (the single
source of truth is `pyproject.toml`). When the package is imported from a source
tree that has **not** been installed, the metadata is unavailable and the value
falls back to `"0.0.0"` — matching `mf_engine`'s behaviour.

## Development

Install the dev tooling and run the checks:

```bash
pip install -e ".[dev]"
pytest                  # tests (with coverage; see below)
ruff check .            # lint
mypy                    # type-check (config in pyproject.toml drives what's checked)
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
├── pyproject.toml                    # repository root
├── README.md
├── LICENSE
├── Makefile
├── Procfile                          # Cloud Run web process
├── .env.example
├── src/
│   └── mf_service/                   # the importable package
│       ├── __init__.py               # exposes `app`, `create_app`, `__version__`
│       ├── __main__.py               # console entry point (uvicorn)
│       ├── app.py                    # the FastAPI application factory
│       ├── py.typed                  # PEP 561 typing marker
│       ├── core/                     # cross-cutting infrastructure
│       │   ├── __init__.py           # re-exports `__version__` (single source)
│       │   ├── identity.py           # service name/description/version from metadata
│       │   ├── config.py             # Settings (pydantic-settings)
│       │   ├── engine.py             # mf_engine seam: version + GNPS processor (lazy)
│       │   └── deps.py               # Depends seams (X-User-Id, repo, storage, processor)
│       ├── domain/                   # storage-agnostic entities (no framework deps)
│       │   ├── __init__.py
│       │   └── batch.py              # Batch entity + BatchStatus + REQUIRED_BATCH_FILES
│       ├── repository/               # persistence contracts + implementations
│       │   ├── __init__.py
│       │   ├── batch.py              # BatchRepository Protocol + InMemory impl + errors
│       │   └── firestore.py          # Firestore-backed BatchRepository (the default)
│       ├── storage/                  # object-store contracts + implementations
│       │   ├── __init__.py
│       │   ├── batch.py              # BatchStorage Protocol + InMemory impl + StoredFile + errors
│       │   └── gcs.py                # Cloud Storage-backed BatchStorage (the default)
│       ├── processing/               # the batch processing pipeline (engine work)
│       │   ├── __init__.py
│       │   └── pipeline.py           # PipelineTask + GNPSAnnotationTask + run_pipeline
│       ├── schemas/                  # request/response models (API contracts)
│       │   ├── __init__.py
│       │   ├── version.py            # VersionResponse
│       │   ├── batch.py              # Batch rename request + batch/file responses
│       │   └── process.py            # ProcessRequest (optional batch_id)
│       └── api/
│           ├── __init__.py           # the API package (grouped by version)
│           └── v1/
│               ├── __init__.py       # aggregates the v1 routes into one router
│               ├── version.py        # GET /version
│               ├── batches.py        # /batches CRUD + upload/background + files + error handlers
│               └── process.py        # POST /process (+ background pipeline) + /process/timeouts
└── tests/
    ├── conftest.py                   # shared fixtures (TestClient, engine stub, batch repo/storage)
    ├── test_package.py               # package surface + app factory
    ├── test_main.py                  # console entry point (uvicorn wiring)
    ├── test_config.py                # Settings + env overrides + cache
    ├── test_engine.py                # mf_engine version seam
    ├── test_version.py               # /version endpoint
    ├── test_deps.py                  # Depends seams (user id, repo/storage providers)
    ├── test_domain.py                # Batch entity (defaults, status, updated)
    ├── test_repository.py            # in-memory BatchRepository + claims
    ├── test_firestore_repository.py  # Firestore repo (fake client)
    ├── test_storage.py               # in-memory + GCS BatchStorage (fake client)
    ├── test_processing.py            # processing pipeline (fake engine)
    ├── test_batches.py               # /batches CRUD + upload + files + scoping + error mapping
    └── test_process.py               # POST /process + /process/timeouts
```

This uses the **src layout** so tests always run against the installed package,
not a stray copy on the path — matching `mf_engine`. The layers are kept
separate: storage-agnostic entities in `domain/`, persistence behind a contract
in `repository/`, file storage behind a contract in `storage/`, the engine
pipeline in `processing/`, API contracts in `schemas/`, cross-cutting plumbing in
`core/`, and routes grouped under `api/v1` (one module per resource). A new endpoint is a new module under `api/v1`
included in its `__init__.py`, plus its schema under `schemas/` —
nothing else in the app changes, and a second API version (`api/v2`) slots in
alongside the first without disturbing it.

## Adding an endpoint

Routes are organised so the service grows without being reshaped:

1. Add the request/response models to a module under `schemas/` (one per
   resource) and export them from `schemas/__init__.py`.
2. Add the route to a new module under `api/v1/` with its own `APIRouter`, and
   include it in `api/v1/__init__.py`.
3. Put any shared plumbing (config, external-service seams, middleware) in
   `core/`. Values with a single source of truth — the service version, the
   engine version — are read through one function there and reused, never
   duplicated.

Cross-cutting concerns (CORS, exception handlers mapping domain errors to HTTP
status codes, request-id/timing middleware) are registered in `create_app`
(`app.py`), which marks the spot. A synchronous, request-time engine route
should stay `def` (not `async def`): the engine does CPU-bound work, which
FastAPI runs in a worker threadpool, whereas a blocking `async def` would stall
the event loop.

## Related repositories

| Repository                                            | Role                                                     |
| ----------------------------------------------------- | -------------------------------------------------------- |
| [mf_app](https://github.com/tarcis-io/mf_app)         | Browser frontend (Next.js) that talks to this service.   |
| [mf_engine](https://github.com/tarcis-io/mf_engine)   | Core processing engine, used internally by `mf_service`. |

`mf_app` reaches `mf_service` server-side over HTTP; its **About** page reads
`GET /version` to display the running `mf_service` and `mf_engine` versions.
