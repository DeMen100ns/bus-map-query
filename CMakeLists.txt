cmake_minimum_required(VERSION 3.20)
project(BusMap VERSION 1.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

option(BUSMAP_ENABLE_LTO "Enable link-time optimization for Release builds" ON)
if(BUSMAP_ENABLE_LTO)
    if(POLICY CMP0138)
        cmake_policy(SET CMP0138 NEW)
    endif()
    include(CheckIPOSupported)
    check_ipo_supported(RESULT busmap_ipo_supported OUTPUT busmap_ipo_error LANGUAGES CXX)
    if(NOT busmap_ipo_supported)
        message(FATAL_ERROR "Release LTO is not supported: ${busmap_ipo_error}\n"
                            "Configure with -DBUSMAP_ENABLE_LTO=OFF to disable it.")
    endif()
endif()
# Initialize every target, including the static library and final executables.
set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE ${BUSMAP_ENABLE_LTO})
message(STATUS "BusMap Release LTO: ${BUSMAP_ENABLE_LTO}")

find_package(Threads REQUIRED)

add_library(busmap
    cpp/src/graph.cpp cpp/src/graph_loader.cpp cpp/src/router.cpp cpp/src/query_pool.cpp
    cpp/src/dijkstra.cpp cpp/src/Dijkstra_baseline.cpp cpp/src/astar.cpp cpp/src/hpa.cpp cpp/src/bihpa.cpp)
target_include_directories(busmap PUBLIC cpp/include)
target_link_libraries(busmap PUBLIC Threads::Threads)
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
    target_compile_options(busmap PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_executable(busmap-query cpp/apps/query_main.cpp)
target_include_directories(busmap-query PRIVATE cpp/src)
target_link_libraries(busmap-query PRIVATE busmap)

add_executable(busmap-bench cpp/apps/benchmark_main.cpp)
target_include_directories(busmap-bench PRIVATE cpp/src)
target_link_libraries(busmap-bench PRIVATE busmap)
target_compile_definitions(busmap-bench PRIVATE
    BUSMAP_BUILD_TYPE="$<CONFIG>"
    BUSMAP_LTO_ENABLED=$<AND:$<CONFIG:Release>,$<BOOL:${BUSMAP_ENABLE_LTO}>>
    BUSMAP_COMPILER="${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}")

include(CTest)
if(BUILD_TESTING)
    add_executable(busmap-dijkstra-test tests/cpp/test_dijkstra.cpp)
    target_link_libraries(busmap-dijkstra-test PRIVATE busmap)
    add_test(NAME dijkstra COMMAND busmap-dijkstra-test)
    set_tests_properties(dijkstra PROPERTIES TIMEOUT 90)
    add_executable(busmap-bihpa-test tests/cpp/test_bihpa.cpp)
    target_link_libraries(busmap-bihpa-test PRIVATE busmap)
    add_test(NAME bihpa COMMAND busmap-bihpa-test)
    set_tests_properties(bihpa PROPERTIES TIMEOUT 90)
    add_executable(busmap-hpa-test tests/cpp/test_hpa.cpp)
    target_link_libraries(busmap-hpa-test PRIVATE busmap)
    add_test(NAME hpa COMMAND busmap-hpa-test)
    set_tests_properties(hpa PROPERTIES TIMEOUT 90)
    add_executable(busmap-loader-test tests/cpp/test_loader.cpp)
    target_link_libraries(busmap-loader-test PRIVATE busmap)
    add_test(NAME loader COMMAND busmap-loader-test "${PROJECT_SOURCE_DIR}")
    add_executable(busmap-query-pool-test tests/cpp/test_query_pool.cpp)
    target_include_directories(busmap-query-pool-test PRIVATE cpp/apps)
    target_link_libraries(busmap-query-pool-test PRIVATE busmap)
    add_test(NAME query_pool COMMAND busmap-query-pool-test "${PROJECT_SOURCE_DIR}")
    set_tests_properties(query_pool PROPERTIES TIMEOUT 30)
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    add_test(NAME concurrency COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_concurrency.py"
             "$<TARGET_FILE:busmap-query>" "${PROJECT_SOURCE_DIR}")
    set_tests_properties(concurrency PROPERTIES TIMEOUT 90)
    add_test(NAME cli COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_cli.py"
             "$<TARGET_FILE:busmap-query>" "${PROJECT_SOURCE_DIR}")
    add_test(NAME benchmark COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_benchmark.py"
             "$<TARGET_FILE:busmap-bench>" "${PROJECT_SOURCE_DIR}")
endif()
