# Alfred

A personal desktop AI assistant. This is a learning project; right now it is
only the skeleton: configuration, logging, a module loader, and one example
module (`hello`).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

## Run

```bash
alfred
alfred --log-level DEBUG
python -m alfred --config alfred.toml
```

## Test

```bash
pytest
```
