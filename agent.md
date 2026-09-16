

I am planning to create a workflow:

stages 
- storage plan
  use queries and schema, and optional statics information
  output: propouced a storage plan
- genaerate basic code
    - fix code until compile and match results
    - use duckdb as a gold standard
- optimize code
    - generate hints
    - apply them 
    - reflect
    - loop until 
Recommend a agent framework to do that.


I am thinking to utilize DSPy RLM to avoid loading files, and use cpplib to extract and work with cpp code   

dspy should have trigger like to compile output of generating and figure out if there an error

I want all interaction with llm to be cached and reused if prompted

allow use prompts from directory (but organize them into json file)

consider a configuration similar to that
----------------------------
{
	"common": {
		"schema_path": "../input/schema.txt",
		"storage_plan_path": "../input/storage_plan.txt",
		"levels": [
			{
				"name": "no_hints",
				"namespace": "basic",
				"file": "storage_layout_basic.hpp"
			},
			{
				"name": "organization_level",
				"namespace": "intermidate",
				"file": "storage_layout_interm.hpp"
			},
			{
				"name": "all_hints",
				"namespace": "full",
				"file": "storage_layout_all.hpp"
			}
		]
	},
	"divide": {
		"policy": "Policy: split inputs into three levels.\n\nLevel 1 — no_hints:\n- Use only schema facts (tables, columns, types, keys, constraints, relationships).\n- Do NOT include storage-plan hints, notes, optimization or implementation advice.\n\nLevel 2 — organization_level:\n- Present organized schema facts using clear headings and groupings (Overview; Tables/Entities; Columns and Types; Keys and Constraints; Relationships).\n- Still do NOT include storage-plan hints or notes.\n\nLevel 3 — all_hints:\n- Include schema facts plus all hints and notes present in `storage_plan_txt`.\n- Do NOT invent new hints, storage decisions, indexes, layouts, encodings, or implementation details.\n- If something is only implied by the storage plan, mark it as \"Implied by storage plan\".\n\nFor each level, also produce a short one-sentence data structure description for downstream code generation.\n\nReturn valid JSON only in this shape, using the exact level names declared above: {\"levels\": {\"<level_name>\": \"<level text>\"}, \"data_structure_descriptions\": {\"<level_name>\": \"<short data structure description>\"}}.",
		"output": {
			"json_path": "../gen/schema_levels.json"
		},
		"llm": {
			"model": "openai/gpt-5.3-codex",
			"temperature": 1,
			"max_tokens": 12000
		},
		"cache": {
			"enabled": true,
			"dir": ".schema_level_cache"
		}
	},
	"hppgen": {
		"levels_path": "../gen/schema_levels.json",
		"out_dir": "../gen/",
		"root_class_name": "Database",
		"active_levels": [
			"no_hints"
		],
		"model": {
			"name": "openai/gpt-5.3-codex",
			"temperature": 1,
			"max_tokens": 12000
		},
		"trace_path": "output/storage_layout_trace.jsonl",
		"trace_stdout": true,
		"enable_wandb": true,
		"enable_weave": true,
		"use_cache": true,
		"refresh_cache": false,
		"cache_dir": ".cache/storage_layout_hpp"
	},
	"query_codegen": {
		"queries_path": "../input/allqueries.txt",
		"gold_dir": "gold",
		"gold_extension": ".csv",
		"dataset_dir": "../dataset/sf0.25",
		"gold_generate_with_duckdb": true,
		"gold_duckdb_path": "gold/gold.duckdb",
		"gold_overwrite": false,
		"gold_install_spatial": true,
		"out_dir": "../gen",
		"output_extension": ".cpp",
		"database_type_name": "Database",
		"database_param_name": "db",
		"query_namespace_prefix": "queries::",
		"result_prefix": "result_",
		"query_function_name": "run",
		"query_ids": [],
		"active_levels": [
			"no_hints"
		],
		"summary_lines": 20,
		"max_summary_chars": 12000,
		"trace_path": "gen3/query_codegen_trace.jsonl",
		"trace_stdout": true,
		"use_cache": true,
		"refresh_cache": false,
		"cache_dir": ".cache/query_codegen",
		"model": {
			"name": "openai/gpt-5.3-codex",
			"model_type": "chat",
			"temperature": 1,
			"max_tokens": null,
			"lm_cache": true,
			"num_retries": 3,
			"api_key_env": "OPENAI_API_KEY",
			"api_base": null,
			"adapter": null,
			"track_usage": true
		}
	}
}
------------------------------


And 
I am planning to create a workflow:
stages 
- storage plan
  use queries and schema, and optional statics information
  output: propouced a storage plan
- genaerate basic code
    - fix code until compile and match results
- optimize code
    - generate hints
    - apply them 
    - reflect

Recommend a agent framework to do that.


I am thinking to utilize DSPy RLM to avoid loading files, and use cpplib to extract and work with cpp code   


I want all interaction with llm to be cached and reused if prompted


consider a configuration similar to that

{
	"common": {
		"schema_path": "../input/schema.txt",
		"storage_plan_path": "../input/storage_plan.txt",
		"levels": [
			{
				"name": "no_hints",
				"namespace": "basic",
				"file": "storage_layout_basic.hpp"
			},
			{
				"name": "organization_level",
				"namespace": "intermidate",
				"file": "storage_layout_interm.hpp"
			},
			{
				"name": "all_hints",
				"namespace": "full",
				"file": "storage_layout_all.hpp"
			}
		]
	},
	"divide": {
		"policy": "Policy: split inputs into three levels.\n\nLevel 1 — no_hints:\n- Use only schema facts (tables, columns, types, keys, constraints, relationships).\n- Do NOT include storage-plan hints, notes, optimization or implementation advice.\n\nLevel 2 — organization_level:\n- Present organized schema facts using clear headings and groupings (Overview; Tables/Entities; Columns and Types; Keys and Constraints; Relationships).\n- Still do NOT include storage-plan hints or notes.\n\nLevel 3 — all_hints:\n- Include schema facts plus all hints and notes present in `storage_plan_txt`.\n- Do NOT invent new hints, storage decisions, indexes, layouts, encodings, or implementation details.\n- If something is only implied by the storage plan, mark it as \"Implied by storage plan\".\n\nFor each level, also produce a short one-sentence data structure description for downstream code generation.\n\nReturn valid JSON only in this shape, using the exact level names declared above: {\"levels\": {\"<level_name>\": \"<level text>\"}, \"data_structure_descriptions\": {\"<level_name>\": \"<short data structure description>\"}}.",
		"output": {
			"json_path": "../gen/schema_levels.json"
		},
		"llm": {
			"model": "openai/gpt-5.3-codex",
			"temperature": 1,
			"max_tokens": 12000
		},
		"cache": {
			"enabled": true,
			"dir": ".schema_level_cache"
		}
	},
	"hppgen": {
		"levels_path": "../gen/schema_levels.json",
		"out_dir": "../gen/",
		"root_class_name": "Database",
		"active_levels": [
			"no_hints"
		],
		"model": {
			"name": "openai/gpt-5.3-codex",
			"temperature": 1,
			"max_tokens": 12000
		},
		"trace_path": "output/storage_layout_trace.jsonl",
		"trace_stdout": true,
		"enable_wandb": true,
		"enable_weave": true,
		"use_cache": true,
		"refresh_cache": false,
		"cache_dir": ".cache/storage_layout_hpp"
	},
	"query_codegen": {
		"queries_path": "../input/allqueries.txt",
		"gold_dir": "gold",
		"gold_extension": ".csv",
		"dataset_dir": "../dataset/sf0.25",
		"gold_generate_with_duckdb": true,
		"gold_duckdb_path": "gold/gold.duckdb",
		"gold_overwrite": false,
		"gold_install_spatial": true,
		"out_dir": "../gen",
		"output_extension": ".cpp",
		"database_type_name": "Database",
		"database_param_name": "db",
		"query_namespace_prefix": "queries::",
		"result_prefix": "result_",
		"query_function_name": "run",
		"query_ids": [],
		"active_levels": [
			"no_hints"
		],
		"summary_lines": 20,
		"max_summary_chars": 12000,
		"trace_path": "gen3/query_codegen_trace.jsonl",
		"trace_stdout": true,
		"use_cache": true,
		"refresh_cache": false,
		"cache_dir": ".cache/query_codegen",
		"model": {
			"name": "openai/gpt-5.3-codex",
			"model_type": "chat",
			"temperature": 1,
			"max_tokens": null,
			"lm_cache": true,
			"num_retries": 3,
			"api_key_env": "OPENAI_API_KEY",
			"api_base": null,
			"adapter": null,
			"track_usage": true
		}
	}
}
-------------------------------------


{

    "levels_path": "schema_levels.json",
  "storage_layout_hpp": "generated/storage_layout_l.hpp",
  "model":     "openai/gpt-5.4-mini",
  "verify": true,
  "compile_check": true,
  "compiler": "g++",
  "cpp_standard": "c++20",
  "max_fix_rounds": 2,
  "fail_on_verify": true,

  "trace_path": "output/storage_layout_trace.jsonl",
  "trace_stdout": true,

  "use_cache": true,
  "refresh_cache": false,
  "cache_dir": ".cache/storage_layout_hpp",
  "no_llm_run":true,
  "gen_project_root": "/home/mk/gen4/",
  "base_dir": "/home/mk/v4/",
  "schema": "./input/schema.txt",
  "storage_plan": "./input/storage_plan.txt",
  "queries_file": "./input/allqueries.txt",
  "build_dir": "/home/mk/gen4/build",
  "actual_output_dir": "./output/",
  "task": "Generate a C++ program that runs each query and produces outputs matching the gold files. The implementation must follow the configured storage plan and schema.\nSpatial code generation specialization:\n- You are implementing an in-memory spatial query engine.\n- Work from the configured schema and storage plan as the source of truth.\n- Do not hard-code assumptions that contradict the schema or storage plan.\n- Use Arrow and Parquet APIs for ingestion.\n- Keep the project buildable after each patch.\n- Prefer simple, portable C++ and CMake for the spatial engine.\n- Create the following implementation files:\n 1. loader_impl.hpp\n 2. loader_impl.cpp\n 3. builder_impl.hpp\n 4. builder_impl.cpp\n- Split the implementation into clear components:\n 1. Loader: implemented in loader_impl.hpp and loader_impl.cpp; loads data from Parquet files using Arrow and Parquet APIs.\n 2. Builder: implemented in builder_impl.hpp and builder_impl.cpp; converts loaded Parquet data into the optimized in-memory layout defined by the storage plan.\n- The generated C++ project must remain buildable with CMake and include verification logic to confirm that queries run and outputs can be compared against the gold files. 5- main program should takes the following inputs: input dir, query_id, and query params. \n - Return only an apply_patch-compatible patch block. \n - Each patch must start with *** Begin Patch and end with *** End Patch. \n - Use only *** Add File:, *** Update File:, and *** Delete File: sections. \n - Do not use diff --git, ---, or +++ file headers.\n - Do not wrap the patch in markdown fences. \n -Use paths relative to the repository root."
  ,
  "gold_command": "uv run ./reference/run_query.py --query-text {query_text} --db_path  ./reference/sf_0.25_1.db --output {gold_output}",   
  "output_extension": ".csv",
  "input_dir": "./dataset/sf0.25/",
  "planner_models": [
    "openai/gpt-5.4-mini"
    
  ],
  "gold_output_dir": "./gold/",
  "patcher_models": [
    "openai/gpt-5.4-mini"
    
  ],
  "dataset_id":"sf_0.25",
  
  "enable_weave": true,
   "enable_wandb": true,
  
  "weave_project_name": "cpp-codegen-all-queries-workflow-2",
  "skip_gold_generation": false,
  "result_json": "./workflow_result.json"
}
------------------------

make a seperate directory (called it agent)
Plan the path first,
allow to have a staged 
and then use dspy RLM with invocation hooks to record trace (make a implmentation)
also make a custom RLM CPP modules based on cpplib


Ask me any first
then plan
