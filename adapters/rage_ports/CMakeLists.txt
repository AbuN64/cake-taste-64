cmake_minimum_required(VERSION 3.28)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(CakeTasteRageSceneAdapter LANGUAGES C)
    include(CTest)
endif()

set(RAGE_PORTS_SOURCE_DIR "" CACHE PATH "Frozen native engine source snapshot")
set(RAGE_PORTS_LIBRARY_DIR "" CACHE PATH "Native engine rw_* static archives")
set(RAGE_PORTS_SOURCE_MANIFEST "" CACHE FILEPATH "Cake Taste immutable source snapshot manifest")
set(RAGE_PORTS_SOURCE_REVISION "" CACHE STRING "Full native source commit for the snapshot")
if(NOT RAGE_PORTS_SOURCE_DIR OR NOT RAGE_PORTS_LIBRARY_DIR OR
   NOT RAGE_PORTS_SOURCE_MANIFEST OR NOT RAGE_PORTS_SOURCE_REVISION)
    message(FATAL_ERROR
        "Set RAGE_PORTS_SOURCE_DIR, RAGE_PORTS_LIBRARY_DIR, RAGE_PORTS_SOURCE_MANIFEST and RAGE_PORTS_SOURCE_REVISION explicitly")
endif()
if(NOT EXISTS "${RAGE_PORTS_SOURCE_MANIFEST}" OR
   IS_DIRECTORY "${RAGE_PORTS_SOURCE_MANIFEST}" OR
   IS_SYMLINK "${RAGE_PORTS_SOURCE_MANIFEST}")
    message(FATAL_ERROR "RAGE_PORTS_SOURCE_MANIFEST must name a regular snapshot manifest")
endif()
file(REAL_PATH "${RAGE_PORTS_SOURCE_DIR}" RAGE_PORTS_SOURCE_DIR)
file(REAL_PATH "${RAGE_PORTS_LIBRARY_DIR}" RAGE_PORTS_LIBRARY_DIR)
file(REAL_PATH "${RAGE_PORTS_SOURCE_MANIFEST}" RAGE_PORTS_SOURCE_MANIFEST)
if(NOT EXISTS "${RAGE_PORTS_SOURCE_DIR}/src/renderer/r_public.h" OR
   NOT EXISTS "${RAGE_PORTS_SOURCE_DIR}/src/platform/sys_public.h" OR
   NOT EXISTS "${RAGE_PORTS_SOURCE_DIR}/tests/test_check.h")
    message(FATAL_ERROR "RAGE_PORTS_SOURCE_DIR is not a complete native source snapshot")
endif()
file(READ "${RAGE_PORTS_SOURCE_MANIFEST}" snapshot_manifest)
string(JSON actual_revision ERROR_VARIABLE manifest_error GET "${snapshot_manifest}" commit)
if(manifest_error)
    message(FATAL_ERROR "Invalid Cake Taste source snapshot manifest: ${manifest_error}")
endif()
string(LENGTH "${RAGE_PORTS_SOURCE_REVISION}" revision_length)
if(NOT revision_length EQUAL 40 OR
   NOT actual_revision STREQUAL RAGE_PORTS_SOURCE_REVISION)
    message(FATAL_ERROR "Native source manifest commit differs from RAGE_PORTS_SOURCE_REVISION")
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
