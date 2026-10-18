# cake-taste-64

Runs bounded N64 experiments and records native-output evidence, using Cake Unbake and Recipe Maker.

> This is a personal tool. It is not officially maintained and may change or break without notice.

## Install

Python 3.13 or newer, in its own virtual environment.

```sh
python3.13 -m venv ~/.venvs/taste && . ~/.venvs/taste/bin/activate
pip install git+https://github.com/AbuN64/cake-taste-64
```

## Usage

```sh
taste --profile project.json profile
taste --help
```

Profiles, commands and the stream are in [docs/usage.md](docs/usage.md).

## Development

```sh
pip install -e '.[dev]' && python -m unittest discover -s tests && ruff check src tests
```

## Credits

Built on [cake-unbake-64](https://github.com/AbuN64/cake-unbake-64) and [recipe-maker-64](https://github.com/AbuN64/recipe-maker-64).

## License

Released under [GNU GPL version 3 or later](LICENSE).
