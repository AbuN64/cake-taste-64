# Runtime thread fixture

`runtime_threads.cpp` is the unmodified `ultramodern/src/threads.cpp` from N64Recomp/N64ModernRuntime commit
`cdf5abbd5026fef5c364c676e4667c45e42b6863` (GPL-3.0, repository COPYING).
Source: https://github.com/N64Recomp/N64ModernRuntime/blob/cdf5abbd5026fef5c364c676e4667c45e42b6863/ultramodern/src/threads.cpp

`test_runtime_thread_context.py` applies the maintained slab patch to this file and compiles the allocation
`osCreateThread` makes for `OSThread::context`, with a stand-in `UltraThreadContext`.
