-- 执行步骤输入值证据（长度/哈希/是否一致/预览）

ALTER TABLE execution_steps
  ADD COLUMN evidence TEXT NULL;
