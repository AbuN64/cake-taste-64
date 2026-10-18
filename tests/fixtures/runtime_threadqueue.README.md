# Runtime queue regression fixture

`runtime_threadqueue.cpp` is the unmodified `ultramodern/src/threadqueue.cpp`
from N64Recomp/N64ModernRuntime commit
`cdf5abbd5026fef5c364c676e4667c45e42b6863` (GPL-3.0, repository COPYING).
Source: https://github.com/N64Recomp/N64ModernRuntime/blob/cdf5abbd5026fef5c364c676e4667c45e42b6863/ultramodern/src/threadqueue.cpp

The test applies the maintained runtime patch to this fixture, compiles the
actual queue operations with minimal memory/type adapters, and exercises
head/middle/tail/absent removal and repeated priority changes in both queue
namespaces. It uses no game code, timing assumptions, or external checkout.
