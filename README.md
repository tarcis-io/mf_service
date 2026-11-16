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

## Configuration

Configuration is centralised in `mf_service.config.Settings` (a
pydantic-settings model). Fields are overridable via an environment variable
with the `MF_SERVICE_` prefix, or a `.env` file — no code reads the environment
directly. See `.env.example` for the available variables.

| Variable                 | Default           | Description                         |
| ------------------------ | ----------------- | ----------------------------------- |
| `MF_SERVICE_HOST`        | `0.0.0.0`         | Bind address for the server.        |
| `MF_SERVICE_PORT`        | `8000`            | Bind port for the server.           |
| `MF_SERVICE_NAME`        | package name      | Overrides the reported name.        |
| `MF_SERVICE_DESCRIPTION` | package summary   | Overrides the reported description. |

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
├── pyproject.toml              # repository root
├── README.md
├── LICENSE
├── Makefile
├── .env.example
├── src/
│   └── mf_service/             # the importable package
│       ├── __init__.py         # exposes `app`, `create_app`, `__version__`
│       ├── __main__.py         # console entry point (uvicorn)
│       ├── app.py              # the FastAPI application factory
│       ├── config.py           # Settings (pydantic-settings)
│       ├── deps.py             # engine-integration seam (Depends providers)
│       ├── schemas.py          # response models (API contracts)
│       ├── py.typed            # PEP 561 typing marker
│       └── api/
│           ├── __init__.py     # the API package (grouped by version)
│           └── v1/
│               ├── __init__.py # aggregates the v1 routes into one router
│               ├── health.py   # GET /health
│               └── version.py  # GET /version
└── tests/
    ├── conftest.py             # shared fixtures (TestClient)
    ├── test_package.py         # package surface + app factory
    ├── test_main.py            # console entry point (uvicorn wiring)
    ├── test_config.py          # Settings + env overrides + cache
    ├── test_deps.py            # engine-integration dependency seam
    ├── test_health.py          # /health endpoint
    └── test_version.py         # /version endpoint
```

This uses the **src layout** so tests always run against the installed package,
not a stray copy on the path — matching `mf_engine`. Routes are grouped under
`api/v1`, so a second API version (or a new endpoint) slots in without
disturbing the existing ones.

## Adding engine-backed endpoints

The service is currently just the `/health` and `/version` identity endpoints;
the next step is routes that call the engine. The seam for that is
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
