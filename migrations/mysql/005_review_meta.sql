-- 视觉审核覆盖度/轮次元数据

ALTER TABLE execution_reviews
  ADD COLUMN review_meta TEXT NULL;
