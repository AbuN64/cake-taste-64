# cake-taste-64

Runs bounded N64 experiments and records native-output evidence.

> This is a personal tool. It is not officially maintained and may change or break without notice.

## Install

Python 3.13 or newer, in its own virtual environment.

```sh
python3.13 -m venv ~/.venvs/taste && . ~/.venvs/taste/bin/activate
pip install git+https://github.com/AbuN64/cake-taste-64
```

The recompiled oracle also needs the MIPS binutils and N64Recomp at its pin, built beside a cake-taste-64 checkout:

```sh
sudo apt install binutils-mips-linux-gnu libsdl2-dev
git clone https://github.com/N64Recomp/N64Recomp && cd N64Recomp
git checkout "$(awk '$1=="N64Recomp"{print $2}' ../cake-taste-64/hosts/n64recomp/PINS)" && git submodule update --init --recursive
git apply ../cake-taste-64/hosts/n64recomp/patches/n64recomp-jtbl-kuseg-alias-and-bounds.diff
cmake -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo && cmake --build build
```

Point the profile's `recomp.tools` at `build/N64Recomp` and `build/RSPRecomp`.

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

## License

Released under [GNU GPL version 3 or later](LICENSE).
