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

The host (`hosts/n64recomp`) also builds against N64ModernRuntime and RT64 at their pins, each with its patch, and runs headless on Mesa software Vulkan:

```sh
sudo apt install cmake ninja-build gcc-14 g++-14 xvfb mesa-vulkan-drivers ffmpeg
for r in N64ModernRuntime rt64; do git clone https://github.com/$( [ $r = rt64 ] && echo rt64 || echo N64Recomp )/$r && (cd $r && git checkout "$(awk -v r=$r '$1==r{print $2}' ../cake-taste-64/hosts/n64recomp/PINS)" && git submodule update --init --recursive); done
git -C N64ModernRuntime apply ../cake-taste-64/hosts/n64recomp/patches/n64modernruntime-{thread-queue-remove,virtual-time}.diff
git -C rt64 apply ../cake-taste-64/hosts/n64recomp/patches/rt64-acclaim-f3dex2-registration.diff
```

Configure with the `N64R_*` inputs in `hosts/n64recomp/CMakeLists.txt`, then point `repro.capture` (`host`, `host_build`, `display_command` starting `Xvfb :N`, `env` with `DISPLAY`, `VK_ICD_FILENAMES=.../lvp_icd.json`, `N64R_ROM`) at it.

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
