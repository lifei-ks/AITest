-- AI 质检最近一次结果落库

ALTER TABLE test_cases
  ADD COLUMN last_review_verdict VARCHAR(20) NULL,
  ADD COLUMN last_review_score INTEGER NULL,
  ADD COLUMN last_reviewed_at DATETIME NULL,
  ADD COLUMN last_review_issues TEXT NULL;
