cmake_minimum_required(VERSION 3.20)
project(cpplib-tests)

# Support C++20 by default, can be overridden with -DCMAKE_CXX_STANDARD=17 (or 23, 14, etc)
if(NOT CMAKE_CXX_STANDARD)
    set(CMAKE_CXX_STANDARD 20)
endif()

set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Enable testing
enable_testing()

# Test fixtures and test binaries would go here
# This CMakeLists.txt will be used to validate generated/modified code
