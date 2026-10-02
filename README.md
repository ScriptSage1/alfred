<p align="center">
  <img src="assets/alfred-logo.png" alt="Alfred logo" width="200">
</p>

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

## Connecting to Obsidian

Alfred talks to Obsidian through the
[Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) community plugin.

1. In Obsidian: Settings → Community plugins → install and enable **Local REST API**.
2. Copy the API key from Settings → Local REST API.
3. Give Alfred the key as an environment variable (never put it in `alfred.toml`):
   ```powershell
   [Environment]::SetEnvironmentVariable("OBSIDIAN_API_KEY", "<your key>", "User")
   ```
   Open a new terminal afterwards so it picks up the variable.
4. Download the plugin's HTTPS certificate into the project folder (Obsidian must be open):
   ```bash
   curl -k https://127.0.0.1:27124/obsidian-local-rest-api.crt -o obsidian-local-rest-api.crt
   ```
5. Run the manual check, which creates `Alfred/connection-test.md` in your vault:
   ```bash
   python scripts/check_obsidian.py
   ```

Settings live in the `[obsidian]` section of `alfred.toml`; `OBSIDIAN_URL` and
`OBSIDIAN_CA_CERT` environment variables override them.
