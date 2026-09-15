# C++ Standards Support Guide

cpplib now supports **C++98, C++11, C++14, C++17, C++20, and C++23** with:
- Configurable compilation standards
- Feature detection per standard
- Dynamic standard switching
- CMake integration

## Quick Start

### Use C++20 by Default
```python
from cpplib import CPPCodebase, CXX20

codebase = CPPCodebase("src", ".", CXX20)
success, msg = codebase.validate()
print(msg)  # "Build successful (C++20)"
```

### Use Different Standards
```python
from cpplib import CPPCodebase, CXX17, CXX20, CXX23

# C++17
codebase = CPPCodebase("src", ".", CXX17)

# C++20
codebase = CPPCodebase("src", ".", CXX20)

# C++23
codebase = CPPCodebase("src", ".", CXX23)
```

### Switch Standards Dynamically
```python
from cpplib import CPPCodebase, CXX17, CXX20

codebase = CPPCodebase("src", ".")  # Defaults to C++17

# Test with C++17
codebase.set_cpp_standard(CXX17)
success, msg = codebase.validate()

# Switch to C++20
codebase.set_cpp_standard(CXX20)
success, msg = codebase.validate()
```

## Supported Standards

| Standard | CMake Flag | Compiler Flag | Released |
|----------|-----------|---------------|----------|
| C++98    | `98`      | `-std=c++98`  | 1998     |
| C++11    | `11`      | `-std=c++11`  | 2011     |
| C++14    | `14`      | `-std=c++14`  | 2014     |
| C++17    | `17`      | `-std=c++17`  | 2017     |
| C++20    | `20`      | `-std=c++20`  | 2020     |
| C++23    | `23`      | `-std=c++23`  | 2023     |

## Feature Matrix

### C++17 (Default)
- Structured bindings: `auto [x, y] = point;`
- Fold expressions
- `if constexpr`
- `std::optional`, `std::variant`
- `std::string_view`
- Filesystem library

### C++20 (Recommended)
**All C++17 features plus:**
- Concepts: `template<std::integral T>`
- Ranges: `nums | std::views::filter(...)`
- Coroutines (experimental)
- Spaceship operator: `<=>` (3-way comparison)
- Designated initializers: `Config{.threads = 8}`
- `consteval` functions
- `requires` clauses

### C++23 (Preview)
**All C++20 features plus:**
- Deducing this / Explicit this
- Multi-dimensional subscripts
- Format library: `std::format`
- Range adapters improvements
- View composition improvements

## API Reference

### Constants
```python
from cpplib import CXX98, CXX11, CXX14, CXX17, CXX20, CXX23
```

### CPPCodebase Methods

#### Constructor
```python
codebase = CPPCodebase(
    root_dir: str,
    cmake_dir: Optional[str] = None,
    cpp_standard: Optional[CppStandard] = None
)
```

#### Set/Get Standard
```python
codebase.set_cpp_standard(CXX20)
std = codebase.get_cpp_standard()  # CppStandard enum
display = codebase.get_cpp_standard_display()  # "C++20"
```

#### Feature Detection
```python
if codebase.check_feature_support("concepts"):
    print("Concepts are available!")

if codebase.check_feature_support("ranges"):
    print("Ranges are available!")
```

#### Validation
```python
success, msg = codebase.validate()
# msg = "Build successful (C++20)"
```

### StandardConfig Class

#### Query Supported Standards
```python
from cpplib import StandardConfig

standards = StandardConfig.SUPPORTED
# [CXX98, CXX11, CXX14, CXX17, CXX20, CXX23]

display = StandardConfig.get_supported_display()
# "C++98, C++11, C++14, C++17, C++20, C++23"
```

#### Get Features for a Standard
```python
features = StandardConfig.get_features(CXX20)
# Returns dict of all features available in C++20 and earlier

has_concepts = StandardConfig.has_feature(CXX20, "concepts")
# True
```

## Usage Examples

### Analyze C++20 Code
```python
from cpplib import CPPCodebase, CXX20, TextReporter

codebase = CPPCodebase("src", ".", CXX20)
reporter = TextReporter(compact=True)

functions = codebase.get_all_functions()
classes = codebase.get_all_classes()
files = codebase.file_index.list_files()

print(reporter.codebase_statistics(len(functions), len(classes), len(files)))
success, msg = codebase.validate()
print(reporter.validation_result(success, msg))
```

### Extract C++20 Functions with Concepts
```python
from cpplib import CPPCodebase, CXX20

codebase = CPPCodebase("src", ".", CXX20)

# Check if concepts are available
if codebase.check_feature_support("concepts"):
    print("✓ Concepts supported")
    piece = codebase.extract_function("my_function")
    if piece:
        print(f"Extracted: {piece.name}")
```

### Multi-Standard Testing
```python
from cpplib import CPPCodebase, CXX17, CXX20, CXX23, TextReporter

codebase = CPPCodebase("src", ".")
reporter = TextReporter(compact=True)

for standard in [CXX17, CXX20, CXX23]:
    codebase.set_cpp_standard(standard)
    success, msg = codebase.validate()
    print(f"{standard}: {msg}")
```

### Feature-Based Code Path
```python
from cpplib import CPPCodebase, CXX20

codebase = CPPCodebase("src", ".", CXX20)

if codebase.check_feature_support("coroutines"):
    # Use async/await style code
    piece = codebase.extract_function("async_function")
else:
    # Use callback style code
    piece = codebase.extract_function("callback_function")
```

## CMakeLists.txt Integration

### Default to C++20
```cmake
cmake_minimum_required(VERSION 3.20)
project(my_project)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(myapp src/main.cpp)
```

### Allow Override
```cmake
cmake_minimum_required(VERSION 3.20)
project(my_project)

if(NOT CMAKE_CXX_STANDARD)
    set(CMAKE_CXX_STANDARD 20)
endif()

set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(myapp src/main.cpp)
```

### Command-Line Override
```bash
cmake .. -DCMAKE_CXX_STANDARD=17
cmake .. -DCMAKE_CXX_STANDARD=23
```

## C++20 Features Example

### Concepts
```cpp
#include <concepts>

// Define a concept
template<typename T>
concept Integral = std::integral<T>;

// Use concept
template<Integral T>
T add(T a, T b) {
    return a + b;
}
```

### Ranges
```cpp
#include <ranges>
#include <vector>

std::vector<int> nums{1, 2, 3, 4, 5};
auto evens = nums | std::views::filter([](int n) { return n % 2 == 0; });
```

### Spaceship Operator
```cpp
class Point {
public:
    int x, y;
    auto operator<=>(const Point&) const = default;
};

Point p1{1, 2};
Point p2{1, 2};
if (p1 <=> p2 == 0) {  // Equal
    // ...
}
```

### Designated Initializers
```cpp
struct Config {
    int threads = 4;
    bool verbose = false;
    std::string name = "default";
};

Config cfg{.threads = 8, .verbose = true};
```

## Feature Detection

Use `check_feature_support()` to make code adaptive:

```python
codebase = CPPCodebase("src", ".", CXX20)

features_to_check = [
    "concepts",
    "ranges",
    "coroutines",
    "spaceship_operator",
    "designated_initializers"
]

for feature in features_to_check:
    if codebase.check_feature_support(feature):
        print(f"✓ {feature}")
```

## Testing Different Standards

Run your tests across multiple standards:

```python
from cpplib import CPPCodebase, StandardConfig, TextReporter

reporter = TextReporter(compact=True)

for standard in StandardConfig.SUPPORTED:
    codebase = CPPCodebase("src", ".", standard)
    success, msg = codebase.validate()
    print(f"{standard.display_name}: {msg}")
```

## Default Standard

- **Default:** C++17
- **Rationale:** Widely supported by compilers, good balance of features
- **Override:** Pass `cpp_standard` to `CPPCodebase` constructor

## Compiler Requirements

| Standard | GCC | Clang | MSVC |
|----------|-----|-------|------|
| C++98    | 3.x | all   | 6.0+ |
| C++11    | 4.7 | 3.1   | 11   |
| C++14    | 5.0 | 3.4   | 12   |
| C++17    | 7.0 | 5.0   | 15.3 |
| C++20    | 10  | 10    | 16.3 |
| C++23    | 13  | 17    | 17.2 |

## See Also

- `demo_cpp20_features.py` - Live examples and feature matrix
- `CPP_STANDARDS_GUIDE.md` - This guide
- `CMakeLists.txt` - Default configuration
