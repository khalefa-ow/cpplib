# cpplib Examples

This directory contains comprehensive examples and demos showcasing cpplib's features.

## Quick Start

Run any example with:
```bash
python examples/demo.py
python examples/demo_reporter.py
python examples/demo_validation.py
python examples/demo_cpp20_features.py
```

## Available Examples

### 1. Basic Demo (`demo.py`)
**Showcase of core cpplib features**

Demonstrates:
- Opening a C++ repository
- Scanning and listing symbols (functions, classes)
- Extracting functions and classes with dependencies
- Analyzing code structure
- Symbol lookup and dependency analysis

Run with:
```bash
python examples/demo.py
```

### 2. Text Reporter Demo (`demo_reporter.py`)
**Comprehensive text output and formatting**

Demonstrates:
- Compact vs. expanded output modes
- Symbol summaries
- Code piece reports with dependencies
- Validation result reporting
- Formatted comparison tables
- High-level analysis reports

Run with:
```bash
python examples/demo_reporter.py
```

### 3. Validation Demo (`demo_validation.py`)
**Error handling and compilation validation**

Demonstrates:
- Syntax validation without full compilation
- Parsing broken/invalid C++ code
- CMake-based full project compilation
- Detailed error reporting
- Error-tolerant parsing
- Build output inspection

Run with:
```bash
python examples/demo_validation.py
```

### 4. C++20/C++23 Features Demo (`demo_cpp20_features.py`)
**C++ standard support and feature detection**

Demonstrates:
- Supported C++ standards (C++98 through C++23)
- Feature matrix by standard
- C++20 project compilation
- C++23-ready code analysis
- Dynamic standard switching
- Feature availability checking

Run with:
```bash
python examples/demo_cpp20_features.py
```

## Feature Coverage

| Feature | Demo |
|---------|------|
| Repository analysis | demo.py |
| Symbol extraction | demo.py |
| Code extraction | demo.py |
| Dependency analysis | demo.py |
| Text output (compact) | demo_reporter.py |
| Text output (expanded) | demo_reporter.py |
| Syntax checking | demo_validation.py |
| Compilation validation | demo_validation.py |
| Error handling | demo_validation.py |
| C++20 features | demo_cpp20_features.py |
| C++23 features | demo_cpp20_features.py |
| Standard switching | demo_cpp20_features.py |

## Common Tasks

### Analyze a C++ Project
```bash
python examples/demo.py
```

### Generate Formatted Reports
```bash
python examples/demo_reporter.py
```

### Test Compilation with Different Standards
```bash
python examples/demo_cpp20_features.py
```

### Check Error Handling
```bash
python examples/demo_validation.py
```

## How to Use These Examples

1. **Run them as-is** to see cpplib in action
2. **Study the code** to understand the API
3. **Modify them** to experiment with cpplib
4. **Adapt them** for your own projects

Each example creates a temporary C++ project, analyzes it, and cleans up automatically.

## Next Steps

- Read the complete documentation in the `docs/` folder
- Check out the API reference in `docs/USAGE.md`
- Review specific guides for features you're interested in:
  - Text output: `docs/REPORTER_GUIDE.md`
  - C++ standards: `docs/CPP_STANDARDS_GUIDE.md`
  - Validation: See `docs/` folder

## Tips

- Examples create temporary directories in `/tmp` - no cleanup needed
- All examples work independently - run them in any order
- Output includes both successful cases and error scenarios
- Use `-v` flag with pytest to see more details (if available)

Enjoy exploring cpplib!
