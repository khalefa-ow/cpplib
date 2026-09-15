# C++ Standards - Quick Reference

## One-Minute Setup

```python
from cpplib import CPPCodebase, CXX20

# Use C++20
codebase = CPPCodebase("src", ".", CXX20)
codebase.validate()  # Compile with C++20
```

## Available Standards

```python
from cpplib import CXX17, CXX20, CXX23, CXX98, CXX11, CXX14

# Use any of these
codebase = CPPCodebase("src", ".", CXX17)
codebase = CPPCodebase("src", ".", CXX20)
codebase = CPPCodebase("src", ".", CXX23)
```

## Quick Commands

### Set Standard
```python
codebase.set_cpp_standard(CXX20)
```

### Get Standard
```python
std = codebase.get_cpp_standard()
print(codebase.get_cpp_standard_display())  # "C++20"
```

### Check Feature
```python
if codebase.check_feature_support("concepts"):
    print("✓ Concepts available")

if codebase.check_feature_support("ranges"):
    print("✓ Ranges available")
```

### Validate with Standard
```python
success, msg = codebase.validate()
# Returns: "Build successful (C++20)"
```

## Key Features by Standard

| Feature | C++17 | C++20 | C++23 |
|---------|-------|-------|-------|
| Concepts | ✗ | ✓ | ✓ |
| Ranges | ✗ | ✓ | ✓ |
| Coroutines | ✗ | ✓ | ✓ |
| `<=>` operator | ✗ | ✓ | ✓ |
| Designated init | ✗ | ✓ | ✓ |
| `constexpr` | ✓ | ✓ | ✓ |
| `if constexpr` | ✓ | ✓ | ✓ |
| Structured binding | ✓ | ✓ | ✓ |
| `std::optional` | ✓ | ✓ | ✓ |
| Filesystem | ✓ | ✓ | ✓ |

## Multi-Standard Testing

```python
from cpplib import StandardConfig, CXX17, CXX20, CXX23

# Test all standards
for std in [CXX17, CXX20, CXX23]:
    codebase.set_cpp_standard(std)
    success, msg = codebase.validate()
    print(f"{std}: {msg}")
```

## C++20 Example Features

### Concepts
```cpp
template<std::integral T>
T add(T a, T b) { return a + b; }
```

### Ranges
```cpp
auto evens = nums | std::views::filter([](int n) { return n % 2 == 0; });
```

### 3-Way Comparison
```cpp
auto result = a <=> b;  // -1, 0, or 1
```

### Designated Initializers
```cpp
Config cfg{.threads = 8, .verbose = true};
```

## CMakeLists.txt

### Default to C++20
```cmake
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
```

### Allow Override
```cmake
if(NOT CMAKE_CXX_STANDARD)
    set(CMAKE_CXX_STANDARD 20)
endif()
```

## API Methods

```python
# Create with standard
CPPCodebase(root_dir, cmake_dir, CXX20)

# Change standard
codebase.set_cpp_standard(CXX20)

# Get current standard
codebase.get_cpp_standard()
codebase.get_cpp_standard_display()

# Check features
codebase.check_feature_support("concepts")

# Validate with current standard
codebase.validate()
```

## Defaults

- **Default Standard:** C++17
- **Default CMake Version:** 3.20+
- **Supported:** C++98, C++11, C++14, C++17, C++20, C++23

## See Full Guide

Read `CPP_STANDARDS_GUIDE.md` for complete documentation.

Run `demo_cpp20_features.py` for live examples.
