cmake_minimum_required(VERSION 3.28)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(CakeTasteRageSceneAdapter LANGUAGES C)
    include(CTest)
endif()

set(RAGE_PORTS_SOURCE_DIR "" CACHE PATH "Frozen native engine source snapshot")
set(RAGE_PORTS_LIBRARY_DIR "" CACHE PATH "Native engine rw_* static archives")
set(RAGE_PORTS_SOURCE_REVISION "" CACHE STRING "Full native source commit for the snapshot")
if(NOT RAGE_PORTS_SOURCE_DIR OR NOT RAGE_PORTS_LIBRARY_DIR OR
   NOT RAGE_PORTS_SOURCE_REVISION)
    message(FATAL_ERROR
        "Set RAGE_PORTS_SOURCE_DIR, RAGE_PORTS_LIBRARY_DIR and RAGE_PORTS_SOURCE_REVISION explicitly")
endif()
file(REAL_PATH "${RAGE_PORTS_SOURCE_DIR}" RAGE_PORTS_SOURCE_DIR)
file(REAL_PATH "${RAGE_PORTS_LIBRARY_DIR}" RAGE_PORTS_LIBRARY_DIR)
if(NOT EXISTS "${RAGE_PORTS_SOURCE_DIR}/src/renderer/r_public.h" OR
   NOT EXISTS "${RAGE_PORTS_SOURCE_DIR}/src/platform/sys_public.h" OR
   NOT EXISTS "${RAGE_PORTS_SOURCE_DIR}/tests/test_check.h")
    message(FATAL_ERROR "RAGE_PORTS_SOURCE_DIR is not a complete native source snapshot")
endif()
if(EXISTS "${RAGE_PORTS_SOURCE_DIR}/SOURCE_COMMIT")
    file(READ "${RAGE_PORTS_SOURCE_DIR}/SOURCE_COMMIT" actual_revision)
    string(STRIP "${actual_revision}" actual_revision)
elseif(EXISTS "${RAGE_PORTS_SOURCE_DIR}/.git")
    execute_process(COMMAND git -C "${RAGE_PORTS_SOURCE_DIR}" rev-parse HEAD
        OUTPUT_VARIABLE actual_revision OUTPUT_STRIP_TRAILING_WHITESPACE
        RESULT_VARIABLE git_result)
    if(NOT git_result EQUAL 0)
        message(FATAL_ERROR "Cannot identify native engine source commit")
    endif()
else()
    message(FATAL_ERROR "Native source snapshot needs SOURCE_COMMIT or .git provenance")
endif()
if(NOT actual_revision STREQUAL RAGE_PORTS_SOURCE_REVISION)
    message(FATAL_ERROR "Native source revision differs from RAGE_PORTS_SOURCE_REVISION")
endif()

find_package(SDL3 CONFIG REQUIRED)
foreach(component common renderer sound input assets platform)
    set(archive "${RAGE_PORTS_LIBRARY_DIR}/librw_${component}.a")
    if(NOT EXISTS "${archive}")
        message(FATAL_ERROR "Missing native engine archive: ${archive}")
    endif()
    add_library(cake_taste_rage_${component} STATIC IMPORTED)
    set_target_properties(cake_taste_rage_${component} PROPERTIES IMPORTED_LOCATION "${archive}")
endforeach()

add_executable(cake-taste-rage-scene-probe scene_probe.c arena_controller.c)
target_compile_features(cake-taste-rage-scene-probe PRIVATE c_std_23)
set_target_properties(cake-taste-rage-scene-probe PROPERTIES
    C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)
target_compile_options(cake-taste-rage-scene-probe PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_include_directories(cake-taste-rage-scene-probe PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}" "${RAGE_PORTS_SOURCE_DIR}/src")
target_link_libraries(cake-taste-rage-scene-probe PRIVATE
    cake_taste_rage_common cake_taste_rage_renderer cake_taste_rage_sound
    cake_taste_rage_input cake_taste_rage_assets cake_taste_rage_platform
    SDL3::SDL3-static m)

if(BUILD_TESTING)
    add_executable(cake-taste-rage-scene-controller-test
        tests/test_scene_controller.c arena_controller.c)
    target_compile_features(cake-taste-rage-scene-controller-test PRIVATE c_std_23)
    set_target_properties(cake-taste-rage-scene-controller-test PROPERTIES
        C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)
    target_compile_options(cake-taste-rage-scene-controller-test PRIVATE
        -Wall -Wextra -Wpedantic -Werror)
    target_include_directories(cake-taste-rage-scene-controller-test PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}" "${RAGE_PORTS_SOURCE_DIR}/src"
        "${RAGE_PORTS_SOURCE_DIR}/tests")
    target_link_libraries(cake-taste-rage-scene-controller-test PRIVATE m)
    add_test(NAME cake_taste_rage_scene_controller
        COMMAND cake-taste-rage-scene-controller-test)
endif()
