-- AICase Core MySQL 初始化 DDL（由 scripts/gen_mysql_ddl.py 生成）
-- 建库后执行：mysql -u root -p <库名> < migrations/mysql/001_init.sql
-- 共 23 张表

SET NAMES utf8mb4;


CREATE TABLE case_generations (
	id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	requirement_id VARCHAR(36), 
	mode VARCHAR(20) NOT NULL, 
	profile_id VARCHAR(36), 
	case_count INTEGER NOT NULL, 
	summary TEXT, 
	snapshot TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_case_generations_project ON case_generations (project_id, created_at);
CREATE INDEX ix_case_generations_project_id ON case_generations (project_id);
CREATE INDEX ix_case_generations_requirement ON case_generations (requirement_id, created_at);
CREATE INDEX ix_case_generations_requirement_id ON case_generations (requirement_id);


CREATE TABLE code_analyses (
	id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	backend_result TEXT, 
	frontend_result TEXT, 
	warnings TEXT, 
	error_message TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_code_analyses_project_id ON code_analyses (project_id);


CREATE TABLE execution_records (
	id VARCHAR(20) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	test_case_id VARCHAR(20) NOT NULL, 
	test_case_title VARCHAR(255) NOT NULL, 
	test_case_snapshot TEXT NOT NULL, 
	batch_id VARCHAR(36), 
	status VARCHAR(20) NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	target_url TEXT, 
	start_time DATETIME NOT NULL, 
	end_time DATETIME, 
	duration_ms INTEGER NOT NULL, 
	error_category VARCHAR(40), 
	error_message TEXT, 
	video_path VARCHAR(500), 
	passed_steps INTEGER NOT NULL, 
	total_steps INTEGER NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_execution_records_batch_id ON execution_records (batch_id);
CREATE INDEX ix_execution_records_project_id ON execution_records (project_id);
CREATE INDEX ix_execution_records_test_case_id ON execution_records (test_case_id);


CREATE TABLE execution_requirements (
	id VARCHAR(36) NOT NULL, 
	execution_id VARCHAR(20) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	requirement_id VARCHAR(36), 
	requirement_version VARCHAR(40), 
	requirement_name VARCHAR(120), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX ix_execution_requirements_execution_id ON execution_requirements (execution_id);
CREATE INDEX ix_execution_requirements_project_id ON execution_requirements (project_id);
CREATE INDEX ix_execution_requirements_requirement_id ON execution_requirements (requirement_id);


CREATE TABLE execution_review_images (
	id VARCHAR(36) NOT NULL, 
	review_id VARCHAR(36) NOT NULL, 
	execution_id VARCHAR(20) NOT NULL, 
	image_index INTEGER NOT NULL, 
	step_index INTEGER, 
	label VARCHAR(255), 
	screenshot VARCHAR(500), 
	verdict VARCHAR(20), 
	analysis TEXT, 
	bbox TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_execution_review_images_execution_id ON execution_review_images (execution_id);
CREATE INDEX ix_execution_review_images_review_id ON execution_review_images (review_id);


CREATE TABLE execution_review_issues (
	id VARCHAR(36) NOT NULL, 
	review_id VARCHAR(36) NOT NULL, 
	execution_id VARCHAR(20) NOT NULL, 
	image_index INTEGER NOT NULL, 
	step_index INTEGER, 
	label VARCHAR(255), 
	screenshot VARCHAR(500), 
	bbox TEXT, 
	problem TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_execution_review_issues_execution_id ON execution_review_issues (execution_id);
CREATE INDEX ix_execution_review_issues_review_id ON execution_review_issues (review_id);


CREATE TABLE execution_reviews (
	id VARCHAR(36) NOT NULL, 
	execution_id VARCHAR(20) NOT NULL, 
	verdict VARCHAR(20) NOT NULL, 
	reason TEXT, 
	model VARCHAR(100), 
	confidence FLOAT, 
	image_count INTEGER NOT NULL, 
	reviewer VARCHAR(20), 
	reviewer_note TEXT, 
	reviewed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX ix_execution_reviews_execution_id ON execution_reviews (execution_id);


CREATE TABLE execution_steps (
	id VARCHAR(36) NOT NULL, 
	execution_id VARCHAR(20) NOT NULL, 
	step_index INTEGER NOT NULL, 
	action VARCHAR(255) NOT NULL, 
	target VARCHAR(255) NOT NULL, 
	step_type VARCHAR(20) NOT NULL, 
	selector_used VARCHAR(500), 
	result VARCHAR(20) NOT NULL, 
	screenshot_before VARCHAR(500), 
	screenshot_after VARCHAR(500), 
	error TEXT, 
	duration_ms INTEGER NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_execution_steps_execution_id ON execution_steps (execution_id);


CREATE TABLE id_sequences (
	name VARCHAR(50) NOT NULL, 
	value INTEGER NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE llm_profiles (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	api_key TEXT, 
	base_url VARCHAR(500), 
	model VARCHAR(120), 
	enabled INTEGER NOT NULL, 
	is_default INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_llm_profiles_kind ON llm_profiles (kind, enabled);


CREATE TABLE mindmaps (
	id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	tree TEXT, 
	statistics TEXT, 
	status VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_mindmaps_project_id ON mindmaps (project_id);


CREATE TABLE projects (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT, 
	source_type VARCHAR(20) NOT NULL, 
	source_path TEXT, 
	git_url VARCHAR(500), 
	git_branch VARCHAR(120), 
	tech_stack TEXT, 
	prd_content TEXT, 
	target_url TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE requirement_cases (
	id VARCHAR(36) NOT NULL, 
	requirement_id VARCHAR(36) NOT NULL, 
	test_case_id VARCHAR(20) NOT NULL, 
	test_point_id VARCHAR(36), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_requirement_cases_case ON requirement_cases (test_case_id);
CREATE INDEX ix_requirement_cases_req ON requirement_cases (requirement_id);
CREATE INDEX ix_requirement_cases_requirement_id ON requirement_cases (requirement_id);
CREATE INDEX ix_requirement_cases_test_case_id ON requirement_cases (test_case_id);
CREATE INDEX ix_requirement_cases_test_point_id ON requirement_cases (test_point_id);


CREATE TABLE requirements (
	id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	version VARCHAR(40) NOT NULL, 
	content TEXT, 
	source_file VARCHAR(255), 
	status VARCHAR(20) NOT NULL, 
	error_message TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_requirements_project ON requirements (project_id, created_at);
CREATE INDEX ix_requirements_project_id ON requirements (project_id);


CREATE TABLE state_machines (
	id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	states TEXT, 
	transitions TEXT, 
	confidence FLOAT NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_state_machines_project_id ON state_machines (project_id);


CREATE TABLE test_case_versions (
	id VARCHAR(36) NOT NULL, 
	test_case_id VARCHAR(20) NOT NULL, 
	version INTEGER NOT NULL, 
	snapshot TEXT NOT NULL, 
	change_note VARCHAR(255), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_test_case_versions_test_case_id ON test_case_versions (test_case_id);


CREATE TABLE test_cases (
	id VARCHAR(20) NOT NULL, 
	project_seq INTEGER NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	module VARCHAR(120) NOT NULL, 
	type VARCHAR(20) NOT NULL, 
	priority VARCHAR(10) NOT NULL, 
	preconditions TEXT, 
	steps TEXT, 
	expected_results TEXT, 
	structured_steps TEXT, 
	test_data TEXT, 
	coverage_refs TEXT, 
	source VARCHAR(20) NOT NULL, 
	confidence FLOAT NOT NULL, 
	quality_score INTEGER NOT NULL, 
	lint_issues TEXT, 
	execution_status VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_test_cases_project_id ON test_cases (project_id);
CREATE INDEX ix_test_cases_project_seq ON test_cases (project_id, project_seq);


CREATE TABLE test_points (
	id VARCHAR(36) NOT NULL, 
	requirement_id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	seq INTEGER NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	category VARCHAR(20) NOT NULL, 
	priority VARCHAR(10) NOT NULL, 
	description TEXT, 
	page_route VARCHAR(255), 
	endpoint VARCHAR(255), 
	fields TEXT, 
	expected TEXT, 
	source VARCHAR(20) NOT NULL, 
	confidence FLOAT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_test_points_project_id ON test_points (project_id);
CREATE INDEX ix_test_points_requirement ON test_points (requirement_id, seq);
CREATE INDEX ix_test_points_requirement_id ON test_points (requirement_id);


CREATE TABLE workflow_case_reviews (
	id VARCHAR(36) NOT NULL, 
	run_id VARCHAR(36) NOT NULL, 
	case_id VARCHAR(20) NOT NULL, 
	round INTEGER NOT NULL, 
	review_status VARCHAR(20) NOT NULL, 
	quality_score INTEGER NOT NULL, 
	issues TEXT, 
	missing_coverage TEXT, 
	suggestions TEXT, 
	deterministic_issues TEXT, 
	source VARCHAR(20) NOT NULL, 
	model VARCHAR(100), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_workflow_case_reviews_case_id ON workflow_case_reviews (case_id);
CREATE INDEX ix_workflow_case_reviews_run_id ON workflow_case_reviews (run_id);
CREATE INDEX ix_workflow_reviews_run_case ON workflow_case_reviews (run_id, case_id, round);


CREATE TABLE workflow_cases (
	id VARCHAR(36) NOT NULL, 
	run_id VARCHAR(36) NOT NULL, 
	case_id VARCHAR(20) NOT NULL, 
	test_point_id VARCHAR(36), 
	position INTEGER NOT NULL, 
	automation_status VARCHAR(20) NOT NULL, 
	review_status VARCHAR(20) NOT NULL, 
	revision_count INTEGER NOT NULL, 
	quality_score INTEGER NOT NULL, 
	first_review_id VARCHAR(36), 
	final_review_id VARCHAR(36), 
	execution_id VARCHAR(20), 
	analysis_id VARCHAR(36), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_workflow_cases_analysis_id ON workflow_cases (analysis_id);
CREATE INDEX ix_workflow_cases_case ON workflow_cases (case_id);
CREATE INDEX ix_workflow_cases_case_id ON workflow_cases (case_id);
CREATE INDEX ix_workflow_cases_execution_id ON workflow_cases (execution_id);
CREATE INDEX ix_workflow_cases_run ON workflow_cases (run_id, position);
CREATE INDEX ix_workflow_cases_run_id ON workflow_cases (run_id);
CREATE INDEX ix_workflow_cases_test_point_id ON workflow_cases (test_point_id);


CREATE TABLE workflow_result_analyses (
	id VARCHAR(36) NOT NULL, 
	run_id VARCHAR(36) NOT NULL, 
	execution_id VARCHAR(20) NOT NULL, 
	case_id VARCHAR(20) NOT NULL, 
	execution_status VARCHAR(20) NOT NULL, 
	failure_category VARCHAR(30) NOT NULL, 
	failure_reason TEXT, 
	evidence TEXT, 
	confidence FLOAT NOT NULL, 
	recommended_action TEXT, 
	needs_human_review INTEGER NOT NULL, 
	source VARCHAR(20) NOT NULL, 
	model VARCHAR(100), 
	raw_response TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_workflow_analyses_run ON workflow_result_analyses (run_id);
CREATE INDEX ix_workflow_result_analyses_case_id ON workflow_result_analyses (case_id);
CREATE INDEX ix_workflow_result_analyses_execution_id ON workflow_result_analyses (execution_id);
CREATE INDEX ix_workflow_result_analyses_run_id ON workflow_result_analyses (run_id);


CREATE TABLE workflow_runs (
	id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	requirement_id VARCHAR(36), 
	requirement_version VARCHAR(40), 
	requirement_name VARCHAR(120), 
	status VARCHAR(20) NOT NULL, 
	current_stage VARCHAR(30), 
	mode VARCHAR(20) NOT NULL, 
	config TEXT, 
	summary TEXT, 
	error_message TEXT, 
	started_at DATETIME, 
	finished_at DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_workflow_runs_project ON workflow_runs (project_id, created_at);
CREATE INDEX ix_workflow_runs_project_id ON workflow_runs (project_id);
CREATE INDEX ix_workflow_runs_requirement ON workflow_runs (requirement_id);
CREATE INDEX ix_workflow_runs_requirement_id ON workflow_runs (requirement_id);


CREATE TABLE workflow_stages (
	id VARCHAR(36) NOT NULL, 
	run_id VARCHAR(36) NOT NULL, 
	stage VARCHAR(30) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	attempt INTEGER NOT NULL, 
	input_summary TEXT, 
	output_summary TEXT, 
	payload TEXT, 
	error_message TEXT, 
	model VARCHAR(100), 
	started_at DATETIME, 
	finished_at DATETIME, 
	duration_ms INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_workflow_stages_run ON workflow_stages (run_id, stage);
CREATE INDEX ix_workflow_stages_run_id ON workflow_stages (run_id);
