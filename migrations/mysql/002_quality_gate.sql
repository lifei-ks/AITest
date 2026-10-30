-- 质量分拆解与结构化场景签名
-- 适用于已执行 001_init.sql 的 MySQL 库

ALTER TABLE test_cases
  ADD COLUMN quality_breakdown TEXT NULL,
  ADD COLUMN scenario_key VARCHAR(64) NULL,
  ADD INDEX ix_test_cases_scenario_key (scenario_key);
