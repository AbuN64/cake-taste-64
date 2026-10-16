# Rage Ports native scene experiment adapter

This adapter runs a bounded native arena preview. It is not the original Rage Wars boot path and does not claim original gameplay. The scene probe, prototype controller, and controller test were extracted from the committed native port at 70eb29208e9bc2a80031404a540225ce15eb1b94. Their logic and public state contract are preserved; only local include paths, the header guard, and the executable usage name changed.

The adapter owns its experiment controller and entry point. It reuses the native engine libraries for common services, indexed rendering, input/replay, sound, arena asset decoding, and platform I/O. It does not copy those engine modules. Cake Taste core owns its direct Cake Unbake and Recipe Maker dependencies and calls this target through explicit project profile build/repro arguments; no Rage Wars facts need to enter the generic core.

Configure the adapter with source, archive, manifest, and revision inputs, plus the SDL3 package from the same engine build:

    cmake -S adapters/rage_ports -B BUILD_DIR -G Ninja \
      -DRAGE_PORTS_SOURCE_DIR=FROZEN_NATIVE_SOURCE \
      -DRAGE_PORTS_LIBRARY_DIR=FROZEN_NATIVE_ARCHIVES \
      -DRAGE_PORTS_SOURCE_MANIFEST=CAKE_TASTE_SOURCE_ENGINE_MANIFEST \
      -DRAGE_PORTS_SOURCE_REVISION=70eb29208e9bc2a80031404a540225ce15eb1b94 \
      -DSDL3_DIR=PINNED_SDL3_CMAKE_DIR
    cmake --build BUILD_DIR --parallel 4
    ctest --test-dir BUILD_DIR --output-on-failure

The source manifest is the immutable per-run JSON emitted by Cake Taste build for source.engine.manifest. CMake reads its commit and fails if it differs from RAGE_PORTS_SOURCE_REVISION. The build worker verifies the manifest contents and source file bytes/modes after gates and before promotion. There is no Git HEAD or ad-hoc marker fallback. CMake also fails if required source headers or any rw_* archive is missing. The library directory and SDL3 package are caller supplied; the adapter has no machine-specific absolute paths.

Public experiment input uses the existing flags: --data-dir, --map, --replay, --frames, --state, and optional --capture. A replay and frame count drive the same tick/controller path as the old native scene probe. The output includes the arena geometry summary, final tick/player state JSON, and a real SDL_GPU PPM when capture is requested. This is a native scene experiment, not menu, match, or original-console startup.
