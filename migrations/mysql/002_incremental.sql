-- AICase Core 增量分析迁移（在已有 23 表库上执行）
-- mysql -u root -p <库名> < migrations/mysql/002_incremental.sql
-- 只新增表，不修改既有表；SQLite 启动时会自动 create_all

SET NAMES utf8mb4;


CREATE TABLE requirement_snapshots (
	id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	requirement_id VARCHAR(36) NOT NULL, 
	analysis_id VARCHAR(36), 
	baseline_requirement_id VARCHAR(36), 
	source VARCHAR(20) NOT NULL, 
	endpoints TEXT, 
	rules TEXT, 
	state_transitions TEXT, 
	security TEXT, 
	warnings TEXT, 
	fingerprint VARCHAR(64), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_requirement_snapshots_project ON requirement_snapshots (project_id, created_at);
CREATE INDEX ix_requirement_snapshots_project_id ON requirement_snapshots (project_id);
CREATE INDEX ix_requirement_snapshots_req ON requirement_snapshots (requirement_id, created_at);
CREATE INDEX ix_requirement_snapshots_requirement_id ON requirement_snapshots (requirement_id);


CREATE TABLE requirement_change_runs (
	id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	requirement_id VARCHAR(36) NOT NULL, 
	baseline_requirement_id VARCHAR(36) NOT NULL, 
	baseline_snapshot_id VARCHAR(36), 
	current_snapshot_id VARCHAR(36), 
	status VARCHAR(20) NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	profile_id VARCHAR(36), 
	summary TEXT, 
	error_message TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_change_runs_baseline ON requirement_change_runs (baseline_requirement_id);
CREATE INDEX ix_change_runs_requirement ON requirement_change_runs (requirement_id, created_at);
CREATE INDEX ix_requirement_change_runs_baseline_requirement_id ON requirement_change_runs (baseline_requirement_id);
CREATE INDEX ix_requirement_change_runs_project_id ON requirement_change_runs (project_id);
CREATE INDEX ix_requirement_change_runs_requirement_id ON requirement_change_runs (requirement_id);


CREATE TABLE requirement_changes (
	id VARCHAR(36) NOT NULL, 
	run_id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	requirement_id VARCHAR(36) NOT NULL, 
	baseline_requirement_id VARCHAR(36) NOT NULL, 
	entity_type VARCHAR(30) NOT NULL, 
	change_type VARCHAR(20) NOT NULL, 
	match_key VARCHAR(255) NOT NULL, 
	old_ref VARCHAR(80), 
	new_ref VARCHAR(80), 
	old_data TEXT, 
	new_data TEXT, 
	reason TEXT, 
	confidence FLOAT NOT NULL, 
	requires_confirmation INTEGER NOT NULL, 
	review_status VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_requirement_changes_baseline_requirement_id ON requirement_changes (baseline_requirement_id);
CREATE INDEX ix_requirement_changes_key ON requirement_changes (run_id, entity_type, match_key);
CREATE INDEX ix_requirement_changes_project_id ON requirement_changes (project_id);
CREATE INDEX ix_requirement_changes_requirement_id ON requirement_changes (requirement_id);
CREATE INDEX ix_requirement_changes_run ON requirement_changes (run_id, change_type);
CREATE INDEX ix_requirement_changes_run_id ON requirement_changes (run_id);


CREATE TABLE requirement_case_changes (
	id VARCHAR(36) NOT NULL, 
	run_id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	requirement_id VARCHAR(36) NOT NULL, 
	baseline_requirement_id VARCHAR(36) NOT NULL, 
	change_id VARCHAR(36), 
	test_case_id VARCHAR(20), 
	baseline_case_id VARCHAR(20), 
	action VARCHAR(20) NOT NULL, 
	regression_impact VARCHAR(20) NOT NULL, 
	regression_reason TEXT, 
	regression_status VARCHAR(20) NOT NULL, 
	confidence FLOAT NOT NULL, 
	dedupe_key VARCHAR(255) NOT NULL, 
	review_status VARCHAR(20) NOT NULL, 
	reason TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_requirement_case_changes_dedupe UNIQUE (run_id, dedupe_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_requirement_case_changes_baseline ON requirement_case_changes (baseline_case_id);
CREATE INDEX ix_requirement_case_changes_baseline_case_id ON requirement_case_changes (baseline_case_id);
CREATE INDEX ix_requirement_case_changes_baseline_requirement_id ON requirement_case_changes (baseline_requirement_id);
CREATE INDEX ix_requirement_case_changes_change_id ON requirement_case_changes (change_id);
CREATE INDEX ix_requirement_case_changes_project_id ON requirement_case_changes (project_id);
CREATE INDEX ix_requirement_case_changes_requirement_id ON requirement_case_changes (requirement_id);
CREATE INDEX ix_requirement_case_changes_run ON requirement_case_changes (run_id, action);
CREATE INDEX ix_requirement_case_changes_run_id ON requirement_case_changes (run_id);
CREATE INDEX ix_requirement_case_changes_target ON requirement_case_changes (requirement_id, dedupe_key);
CREATE INDEX ix_requirement_case_changes_test_case_id ON requirement_case_changes (test_case_id);
