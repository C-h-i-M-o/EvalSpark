CREATE DATABASE IF NOT EXISTS multichateval CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE multichateval;

CREATE TABLE IF NOT EXISTS users (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  username VARCHAR(64) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role VARCHAR(16) NOT NULL DEFAULT 'user',
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  last_login_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

SET SESSION sql_mode = CONCAT_WS(',', @@SESSION.sql_mode, 'NO_AUTO_VALUE_ON_ZERO');

INSERT INTO users (id, username, password_hash)
VALUES (0, 'anonymous', 'anonymous')
ON DUPLICATE KEY UPDATE
  username = VALUES(username),
  password_hash = VALUES(password_hash),
  status = 'disabled';

ALTER TABLE users AUTO_INCREMENT = 1;

CREATE TABLE IF NOT EXISTS conversations (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT NULL,
  title VARCHAR(120) NOT NULL DEFAULT '新评测会话',
  mode VARCHAR(32) NOT NULL DEFAULT 'compare',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_conversations_user FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS model_providers (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(64) NOT NULL UNIQUE,
  base_url VARCHAR(255) NULL,
  api_key_encrypted VARCHAR(512) NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS model_configs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  provider_id BIGINT NOT NULL,
  model_name VARCHAR(120) NOT NULL,
  display_name VARCHAR(120) NOT NULL,
  price_input DECIMAL(16, 6) NOT NULL DEFAULT 0,
  price_output DECIMAL(16, 6) NOT NULL DEFAULT 0,
  price_cache_hit DECIMAL(16, 6) NOT NULL DEFAULT 0,
  price_cache_creation DECIMAL(16, 6) NOT NULL DEFAULT 0,
  currency VARCHAR(3) NOT NULL DEFAULT 'CNY',
  temperature DECIMAL(4, 2) NOT NULL DEFAULT 0.70,
  timeout_seconds INT NOT NULL DEFAULT 60,
  notes TEXT NULL,
  max_tokens INT NOT NULL DEFAULT 4096,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  CONSTRAINT fk_model_configs_provider FOREIGN KEY (provider_id) REFERENCES model_providers(id)
);

CREATE TABLE IF NOT EXISTS evaluation_tasks (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  conversation_id BIGINT NULL,
  user_id BIGINT NOT NULL,
  prompt TEXT NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  visibility VARCHAR(16) NOT NULL DEFAULT 'public',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  CONSTRAINT fk_evaluation_tasks_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id),
  CONSTRAINT fk_evaluation_tasks_user FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS model_responses (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id BIGINT NOT NULL,
  model_config_id BIGINT NULL,
  answer_text TEXT NOT NULL,
  latency_ms INT NOT NULL DEFAULT 0,
  input_tokens INT NOT NULL DEFAULT 0,
  output_tokens INT NOT NULL DEFAULT 0,
  cache_hit_tokens INT NOT NULL DEFAULT 0,
  cache_creation_tokens INT NOT NULL DEFAULT 0,
  total_tokens INT NOT NULL DEFAULT 0,
  input_cost DECIMAL(18, 10) NOT NULL DEFAULT 0,
  output_cost DECIMAL(18, 10) NOT NULL DEFAULT 0,
  cache_hit_cost DECIMAL(18, 10) NOT NULL DEFAULT 0,
  cache_creation_cost DECIMAL(18, 10) NOT NULL DEFAULT 0,
  estimated_cost DECIMAL(18, 10) NOT NULL DEFAULT 0,
  currency VARCHAR(3) NOT NULL DEFAULT 'CNY',
  config_snapshot JSON NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'success',
  error_message TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_model_responses_task FOREIGN KEY (task_id) REFERENCES evaluation_tasks(id),
  CONSTRAINT fk_model_responses_model_config FOREIGN KEY (model_config_id) REFERENCES model_configs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS evaluation_results (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  response_id BIGINT NOT NULL,
  relevance_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
  completeness_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
  clarity_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
  format_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
  safety_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
  accuracy_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
  usefulness_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
  objective_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
  rule_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
  judge_score DECIMAL(4, 2) NULL,
  final_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
  judge_comment TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_evaluation_results_response FOREIGN KEY (response_id) REFERENCES model_responses(id)
);

CREATE TABLE IF NOT EXISTS user_feedback (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  response_id BIGINT NOT NULL,
  feedback_type VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_user_feedback_user_response (user_id, response_id),
  CONSTRAINT fk_user_feedback_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_user_feedback_response FOREIGN KEY (response_id) REFERENCES model_responses(id)
);

CREATE TABLE IF NOT EXISTS user_comments (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  response_id BIGINT NOT NULL,
  content TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_comments_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_user_comments_response FOREIGN KEY (response_id) REFERENCES model_responses(id)
);

CREATE TABLE IF NOT EXISTS user_token_quotas (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT NOT NULL UNIQUE,
  daily_limit INT NOT NULL DEFAULT 100000,
  CONSTRAINT fk_user_token_quotas_user FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS daily_user_token_usage (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  usage_date DATE NOT NULL,
  total_tokens INT NOT NULL DEFAULT 0,
  UNIQUE KEY uq_daily_user_token_usage_user_date (user_id, usage_date),
  CONSTRAINT fk_daily_user_token_usage_user FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS token_usage_logs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  task_id BIGINT NOT NULL,
  response_id BIGINT NOT NULL UNIQUE,
  model_config_id BIGINT NULL,
  usage_date DATE NOT NULL,
  total_tokens INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_token_usage_logs_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_token_usage_logs_task FOREIGN KEY (task_id) REFERENCES evaluation_tasks(id),
  CONSTRAINT fk_token_usage_logs_response FOREIGN KEY (response_id) REFERENCES model_responses(id),
  CONSTRAINT fk_token_usage_logs_model_config FOREIGN KEY (model_config_id) REFERENCES model_configs(id) ON DELETE SET NULL
);

-- 上述初始化结构对应此 Alembic 基线，后续版本统一交给迁移容器升级。
CREATE TABLE IF NOT EXISTS alembic_version (
  version_num VARCHAR(32) NOT NULL,
  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

INSERT INTO alembic_version (version_num)
SELECT '20260612_01'
WHERE NOT EXISTS (SELECT 1 FROM alembic_version);
