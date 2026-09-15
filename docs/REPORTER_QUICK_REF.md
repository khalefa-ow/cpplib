# Text Reporter - Quick Reference

## One-Minute Setup

```python
from cpplib import CPPCodebase, TextReporter

codebase = CPPCodebase("src", ".")
reporter = TextReporter(compact=True)  # or False for detailed output
```

## Available Methods

| Method | Purpose | Example Output |
|--------|---------|-----------------|
| `symbols_summary(funcs, classes)` | List all symbols | `Functions (7): main, add, subtract, ...` |
| `codebase_statistics(n_funcs, n_classes, n_files)` | Project overview | `Stats: 3 files \| 7 functions \| 1 classes` |
| `code_piece_summary(piece)` | Function/class details | `FUNCTION: add @ file.cpp:10-15 \| deps:2` |
| `dependency_report(name, deps)` | What a symbol depends on | `Dependencies for 'add': types: int` |
| `validation_result(success, msg)` | Build status | `✓ PASS: Build successful` |
| `extraction_summary(pieces)` | List extracted pieces | `Extracted 3 pieces: • add, subtract, ...` |
| `comparison_table(items, cols)` | Formatted comparison | Table with aligned columns |

## Quick Examples

### List all symbols
```python
funcs = codebase.get_all_functions()
clses = codebase.get_all_classes()
print(reporter.symbols_summary(funcs, clses))
```

### Project overview
```python
files = codebase.file_index.list_files()
print(reporter.codebase_statistics(len(funcs), len(clses), len(files)))
```

### Extract and report function
```python
piece = codebase.extract_function("myFunc")
print(reporter.code_piece_summary(piece))
print(reporter.dependency_report("myFunc", codebase.get_dependencies("myFunc")))
```

### Validation report
```python
success, msg = codebase.validate()
print(reporter.validation_result(success, msg))
```

### High-level analysis
```python
from cpplib import AnalysisReporter
analysis = AnalysisReporter(codebase, compact=True)
print(analysis.full_analysis())
print(analysis.symbol_details("myFunc"))
```

## Output Modes

| Mode | Use Case | Example |
|------|----------|---------|
| `compact=True` | CLI, logs, one-liners | `FUNCTION: add @ file.cpp:10-15 \| deps:2` |
| `compact=False` | Review, docs, detailed | Multi-line with full info |

## Configuration

```python
# Compact output, 100 char width (default)
reporter = TextReporter(compact=True, max_width=100)

# Expanded output, 120 char width
reporter = TextReporter(compact=False, max_width=120)
```

## Symbol Report Template

```python
# Get symbols
functions = codebase.get_all_functions()
classes = codebase.get_all_classes()
files = codebase.file_index.list_files()

# Create reporter
reporter = TextReporter(compact=True)

# Print everything
print("=== Codebase Analysis ===\n")
print(reporter.codebase_statistics(len(functions), len(classes), len(files)))
print()
print(reporter.symbols_summary(functions, classes))
print()

# Validate
success, msg = codebase.validate()
print(reporter.validation_result(success, msg))
```

## Full Demo

Run all examples:
```bash
python demo_reporter.py
```

## See Also

- `REPORTER_GUIDE.md` - Full documentation
- `demo_reporter.py` - 10 complete examples
- `TEXT_OUTPUT_SUMMARY.md` - Feature overview
