-- AICase Core MySQL 初始化 DDL（由 scripts/gen_mysql_ddl.py 生成）
-- 建库后执行：mysql -u root -p <库名> < migrations/mysql/001_init.sql
-- 共 15 张表

SET NAMES utf8mb4;


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
