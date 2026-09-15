# cpplib Documentation

Complete documentation for cpplib - C++ Code Generation & Manipulation Library.

## Start Here

1. **[USAGE.md](USAGE.md)** - Complete API reference and guide
2. **Examples in `../examples/`** - See cpplib in action

## Documentation Index

### Getting Started
- **[USAGE.md](USAGE.md)** - Complete usage guide and API reference

### Text Output & Reporting
- **[REPORTER_GUIDE.md](REPORTER_GUIDE.md)** - Full text reporter API documentation
- **[REPORTER_QUICK_REF.md](REPORTER_QUICK_REF.md)** - One-page quick reference

### C++ Standards Support
- **[CPP_STANDARDS_GUIDE.md](CPP_STANDARDS_GUIDE.md)** - Complete C++ standards reference
- **[CPP_STANDARDS_QUICK_REF.md](CPP_STANDARDS_QUICK_REF.md)** - Quick C++ standards reference
- **[CPP_STANDARDS_SUMMARY.md](CPP_STANDARDS_SUMMARY.md)** - C++ standards feature overview

### Feature Summaries
- **[FEATURE_SUMMARY.md](FEATURE_SUMMARY.md)** - Text output feature summary
- **[TEXT_OUTPUT_SUMMARY.md](TEXT_OUTPUT_SUMMARY.md)** - Text reporter technical overview

### Testing & Automation
- **[TESTING_FROM_ANYWHERE.md](TESTING_FROM_ANYWHERE.md)** - Testing guide
- **[UV_TESTING.md](UV_TESTING.md)** - Testing with uv automation

## Quick Reference

### Create a CPPCodebase
```python
from cpplib import CPPCodebase

codebase = CPPCodebase("src", ".")
```

### Extract Code
```python
function = codebase.extract_function("myFunction")
cls = codebase.extract_class("MyClass")
```

### Generate Reports
```python
from cpplib import TextReporter

reporter = TextReporter(compact=True)
functions = codebase.get_all_functions()
print(reporter.symbols_summary(functions, []))
```

### Use C++20
```python
from cpplib import CPPCodebase, CXX20

codebase = CPPCodebase("src", ".", CXX20)
success, msg = codebase.validate()
```

## Documentation by Topic

### Core Functionality
- Symbol extraction (functions, classes)
- Code extraction and composition
- Dependency analysis
- Compilation validation

See: [USAGE.md](USAGE.md)

### Text Output
- Compact and expanded modes
- Symbol summaries
- Code piece reports
- Dependency reports
- Validation results

See: [REPORTER_GUIDE.md](REPORTER_GUIDE.md), [REPORTER_QUICK_REF.md](REPORTER_QUICK_REF.md)

### C++ Standards
- Support for C++98, C++11, C++14, C++17, C++20, C++23
- Feature detection
- Dynamic standard switching
- CMake integration

See: [CPP_STANDARDS_GUIDE.md](CPP_STANDARDS_GUIDE.md), [CPP_STANDARDS_QUICK_REF.md](CPP_STANDARDS_QUICK_REF.md)

### Testing & Validation
- Syntax checking
- CMake compilation
- Error handling
- Multi-standard testing

See: [TESTING_FROM_ANYWHERE.md](TESTING_FROM_ANYWHERE.md)

## Supported C++ Standards

| Standard | Default | Compiler | Features |
|----------|---------|----------|----------|
| C++98    | - | GCC 3.x | Foundation |
| C++11    | - | GCC 4.7 | Auto, lambda |
| C++14    | - | GCC 5.0 | Auto return types |
| C++17    | ✓ | GCC 7.0 | Structured bindings, if constexpr |
| C++20    | - | GCC 10 | Concepts, ranges, coroutines |
| C++23    | - | GCC 13 | Enhanced features |

## Main Features

### ✓ Code Analysis
- Parse C++ projects with tree-sitter
- Extract functions and classes
- Track dependencies
- Analyze symbol relationships

### ✓ Code Extraction
- Extract functions with dependencies
- Extract classes with methods
- Compose code units
- Handle forward declarations

### ✓ Compilation Validation
- CMake-based builds
- Syntax checking
- Error reporting
- Multi-standard support

### ✓ Text Output
- Compact format (CLI/logs)
- Expanded format (documentation)
- Formatted tables
- Symbol summaries

### ✓ Modern C++ Support
- C++20 concepts
- C++20 ranges
- C++20 coroutines
- C++23 features (preview)

## Examples

All examples are in the `examples/` folder:

- `demo.py` - Basic feature showcase
- `demo_reporter.py` - Text output examples
- `demo_validation.py` - Error handling and compilation
- `demo_cpp20_features.py` - C++ standards and features

Run examples:
```bash
python examples/demo.py
python examples/demo_reporter.py
python examples/demo_validation.py
python examples/demo_cpp20_features.py
```

## Common Tasks

### Analyze a C++ Project
```python
from cpplib import CPPCodebase

codebase = CPPCodebase("src", ".")
functions = codebase.get_all_functions()
classes = codebase.get_all_classes()

for func in functions:
    print(f"{func.name} in {func.file_path}")
```

See: [USAGE.md](USAGE.md)

### Extract a Function
```python
piece = codebase.extract_function("myFunction")
if piece:
    print(f"Function: {piece.name}")
    print(f"Dependencies: {piece.dependencies}")
```

See: [USAGE.md](USAGE.md)

### Generate a Report
```python
from cpplib import TextReporter

reporter = TextReporter(compact=True)
print(reporter.symbols_summary(functions, classes))
```

See: [REPORTER_GUIDE.md](REPORTER_GUIDE.md)

### Compile with C++20
```python
from cpplib import CPPCodebase, CXX20

codebase = CPPCodebase("src", ".", CXX20)
success, msg = codebase.validate()
```

See: [CPP_STANDARDS_GUIDE.md](CPP_STANDARDS_GUIDE.md)

## API Structure

```
cpplib/
├── codebase.py              # Main CPPCodebase class
├── parser/                  # C++ parsing
├── semantic/                # Symbol analysis
├── pieces/                  # Code extraction
├── generator/               # Code generation
├── validator/               # Compilation validation
│   └── cpp_standard.py      # C++ standards support
└── reporter/                # Text output
    └── text_reporter.py     # TextReporter class
```

## Help & Support

- Read the relevant documentation for your task
- Check the examples in `../examples/`
- Review the API reference in [USAGE.md](USAGE.md)
- See specific guides for features:
  - Text output: [REPORTER_GUIDE.md](REPORTER_GUIDE.md)
  - C++ standards: [CPP_STANDARDS_GUIDE.md](CPP_STANDARDS_GUIDE.md)
  - Validation: [TESTING_FROM_ANYWHERE.md](TESTING_FROM_ANYWHERE.md)

## File Organization

```
docs/
├── README.md                          (this file)
├── USAGE.md                           (complete guide)
├── REPORTER_GUIDE.md                  (text output)
├── REPORTER_QUICK_REF.md              (text output quick ref)
├── CPP_STANDARDS_GUIDE.md             (C++ standards)
├── CPP_STANDARDS_QUICK_REF.md         (C++ standards quick ref)
├── CPP_STANDARDS_SUMMARY.md           (C++ standards overview)
├── FEATURE_SUMMARY.md                 (text output overview)
├── TEXT_OUTPUT_SUMMARY.md             (text output technical)
├── TESTING_FROM_ANYWHERE.md           (testing)
└── UV_TESTING.md                      (testing with uv)
```

## Contributing

When documenting new features:
1. Add usage examples to relevant guide
2. Update the feature matrix if applicable
3. Add a quick reference if it's a major feature
4. Update this README with new documentation

Happy coding!
