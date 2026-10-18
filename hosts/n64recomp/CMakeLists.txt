cmake_minimum_required(VERSION 3.20)
project(n64recomp_host C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 17)

# Inputs come from the command line; the repo carries no host paths or game data.
#   -DN64R_PINS=<PINS file>        lines "N64ModernRuntime <sha>" and "rt64 <sha>"
#   -DN64R_GEN_DIR=<dir>           recompiled output (funcs/, rsp/rsp_audio.cpp)
#   -DN64R_PROFILE=<oracle.json>   entrypoint, rom_sha1, version
#   -DN64R_RUNTIME_SRC / -DN64R_RT64_SRC  optional pre-fetched checkouts (else FetchContent at the pin)
#   -DSDL2_DIR=<...>               SDL2 cmake config (RT64 requires SDL2)
foreach(v N64R_PINS N64R_GEN_DIR N64R_PROFILE)
  if(NOT DEFINED ${v})
    message(FATAL_ERROR "${v} is required")
  endif()
endforeach()

file(STRINGS "${N64R_PINS}" _pins)
foreach(l IN LISTS _pins)
  if(l MATCHES "^(N64ModernRuntime|rt64) ([0-9a-f]+)")
    set(PIN_${CMAKE_MATCH_1} ${CMAKE_MATCH_2})
  endif()
endforeach()

include(FetchContent)
if(N64R_RUNTIME_SRC)
  set(_rt_src ${N64R_RUNTIME_SRC})
else()
  FetchContent_Declare(n64mr GIT_REPOSITORY https://github.com/N64Recomp/N64ModernRuntime.git GIT_TAG ${PIN_N64ModernRuntime})
  FetchContent_Populate(n64mr)
  set(_rt_src ${n64mr_SOURCE_DIR})
endif()
if(N64R_RT64_SRC)
  set(_rt64_src ${N64R_RT64_SRC})
else()
  FetchContent_Declare(rt64 GIT_REPOSITORY https://github.com/rt64/rt64.git GIT_TAG ${PIN_rt64} GIT_SUBMODULES_RECURSE TRUE)
  FetchContent_Populate(rt64)
  set(_rt64_src ${rt64_SOURCE_DIR})
endif()

# Functions N64Recomp refused or ignored have no body; give them logging stubs so the rest links.
# N64R_REFUSED is an optional TSV (header line, then name<TAB>reason).
set(_stub_c "#include \"recomp.h\"\nvoid n64host_unimplemented(const char*);\n")
set(_stub_h "#pragma once\n")
if(NOT "${N64R_REFUSED}" STREQUAL "")
  file(STRINGS "${N64R_REFUSED}" _ref)
  list(POP_FRONT _ref)
  foreach(l IN LISTS _ref)
    string(REGEX MATCH "^[A-Za-z0-9_]+" n "${l}")
    if(NOT "${n}" STREQUAL "")
      string(APPEND _stub_h "void ${n}(uint8_t* rdram, recomp_context* ctx);\n")
      string(APPEND _stub_c "void ${n}(uint8_t* rdram, recomp_context* ctx) { n64host_unimplemented(\"${n}\"); }\n")
    endif()
  endforeach()
endif()
file(MAKE_DIRECTORY ${CMAKE_BINARY_DIR}/gen)
file(WRITE ${CMAKE_BINARY_DIR}/gen/refused_stubs.c "${_stub_c}")
file(WRITE ${CMAKE_BINARY_DIR}/gen/refused_decls.h "${_stub_h}")

# Profile -> generated header (entrypoint, ROM hash); no constants live in sources.
file(READ "${N64R_PROFILE}" _pj)
string(JSON N64R_ENTRY GET "${_pj}" entrypoint)
string(JSON N64R_VERSION GET "${_pj}" version)
string(JSON N64R_SHA1 GET "${_pj}" rom_sha1)
# Boot facts: continuation of the refused entry stub, initial stack, RDRAM size, static TLB mappings.
string(JSON N64R_BOOT_MAIN GET "${_pj}" boot main)
string(JSON N64R_BOOT_SP GET "${_pj}" boot sp)
string(JSON N64R_RDRAM_SIZE GET "${_pj}" boot rdram_size)
string(JSON _ntlb LENGTH "${_pj}" boot tlb)
set(_tlb "")
set(_tlbn 0)
if(_ntlb GREATER 0)
  math(EXPR _last "${_ntlb}-1")
  foreach(i RANGE ${_last})
    string(JSON v GET "${_pj}" boot tlb ${i} vaddr)
    string(JSON p GET "${_pj}" boot tlb ${i} paddr)
    string(JSON z GET "${_pj}" boot tlb ${i} size)
    string(APPEND _tlb "{${v}u,${p}u,${z}u},")
    math(EXPR _tlbn "${_tlbn}+1")
  endforeach()
endif()
file(MAKE_DIRECTORY ${CMAKE_BINARY_DIR}/gen)
file(WRITE ${CMAKE_BINARY_DIR}/gen/host_profile.h
"#pragma once\n#include <stdint.h>\n#define HOST_ENTRY_ADDR ${N64R_ENTRY}u\n#define HOST_VERSION \"${N64R_VERSION}\"\n#define HOST_ROM_SHA1 \"${N64R_SHA1}\"\n#define HOST_BOOT_MAIN ${N64R_BOOT_MAIN}u\n#define HOST_BOOT_SP ${N64R_BOOT_SP}u\n#define HOST_RDRAM_SIZE ${N64R_RDRAM_SIZE}u\n#define HOST_TLB_COUNT ${_tlbn}\nstatic const uint32_t host_tlb[][3] = {${_tlb}{0,0,0}};\n")
# Runtime DMA uses the original ROM, never the recompiler's synthetic analysis image.
find_package(Python3 REQUIRED COMPONENTS Interpreter)
execute_process(COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/boot_profile.py
  ${N64R_PROFILE} ${CMAKE_BINARY_DIR}/gen/boot_profile.h COMMAND_ERROR_IS_FATAL ANY)
execute_process(COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/input_profile.py
  ${N64R_PROFILE} ${CMAKE_BINARY_DIR}/gen/host_input_profile.h COMMAND_ERROR_IS_FATAL ANY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  ${N64R_PROFILE} ${N64R_REFUSED} ${CMAKE_CURRENT_SOURCE_DIR}/boot_profile.py ${CMAKE_CURRENT_SOURCE_DIR}/input_profile.py)
set(NFD_PORTAL ON CACHE BOOL "" FORCE)  # dbus portal; avoids a GTK3 dependency
set(RT64_STATIC ON CACHE BOOL "" FORCE)
set(RT64_SDL_WINDOW_VULKAN ON CACHE BOOL "" FORCE)
add_subdirectory(${_rt_src}/ultramodern ultramodern)
add_subdirectory(${_rt_src}/librecomp librecomp)
add_subdirectory(${_rt64_src} rt64)

file(GLOB GEN_FUNCS CONFIGURE_DEPENDS ${N64R_GEN_DIR}/funcs/*.c)
add_executable(n64host
  main.cpp render_rt64.cpp audio_sdl.cpp input_sdl.cpp libultra_gaps.c addrmap.c cop0_adapter.c pi_adapter.cpp vi_adapter.cpp tlb_adapter.c si_adapter.cpp
  ${CMAKE_BINARY_DIR}/gen/refused_stubs.c
  ${GEN_FUNCS} ${N64R_GEN_DIR}/rsp/rsp_audio.cpp)
set_source_files_properties(${GEN_FUNCS} PROPERTIES COMPILE_OPTIONS "-w;-O1")
target_include_directories(n64host PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/probe ${CMAKE_BINARY_DIR}/gen
  ${N64R_GEN_DIR}/funcs ${_rt_src}/N64Recomp/include ${_rt64_src}/include
  ${_rt_src}/librecomp/include ${_rt_src}/ultramodern/include ${_rt_src}/thirdparty ${_rt_src}/thirdparty/xxHash ${_rt64_src}/src ${_rt64_src}/src/contrib ${_rt64_src}/src/contrib/hlslpp/include)
set_source_files_properties(${N64R_GEN_DIR}/rsp/rsp_audio.cpp PROPERTIES COMPILE_OPTIONS "-msse4.1")
find_package(SDL2 REQUIRED)
target_link_libraries(n64host PRIVATE librecomp ultramodern rt64 ${SDL2_LIBRARIES})
target_compile_definitions(n64host PRIVATE HLSL_CPU FFX_GCC RT64_SDL_WINDOW_VULKAN IMGUI_IMPL_VULKAN_NO_PROTOTYPES)
target_link_options(n64host PRIVATE -Wl,--wrap=cop0_status_write
  -Wl,--wrap=osSetEventMesg_recomp -Wl,--wrap=osPiRawStartDma_recomp
  -Wl,--wrap=osCreateViManager_recomp
  -Wl,--wrap=osAiGetStatus_recomp -Wl,--wrap=osAiSetNextBuffer_recomp)
