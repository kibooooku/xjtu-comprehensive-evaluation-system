CREATE TABLE app_user (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(100) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE class_group (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE class_membership (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    class_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    role VARCHAR(32) NOT NULL,
    CONSTRAINT ck_membership_role CHECK (role IN ('STUDENT', 'CLASS_COMMITTEE')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_membership_class FOREIGN KEY (class_id) REFERENCES class_group (id),
    CONSTRAINT fk_membership_user FOREIGN KEY (user_id) REFERENCES app_user (id),
    CONSTRAINT uk_membership_class_user UNIQUE (class_id, user_id)
);
CREATE INDEX idx_membership_user ON class_membership (user_id);

CREATE TABLE declaration (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    class_membership_id BIGINT NOT NULL,
    title VARCHAR(200) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    CONSTRAINT ck_declaration_status CHECK (status IN ('DRAFT')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_declaration_membership FOREIGN KEY (class_membership_id) REFERENCES class_membership (id)
);
CREATE INDEX idx_declaration_membership_created ON declaration (class_membership_id, created_at);

CREATE TABLE declaration_pdf (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    declaration_id BIGINT NOT NULL UNIQUE,
    original_filename VARCHAR(255) NOT NULL,
    media_type VARCHAR(100) NOT NULL,
    size_bytes BIGINT NOT NULL,
    storage_key VARCHAR(500) NOT NULL UNIQUE,
    sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_pdf_declaration FOREIGN KEY (declaration_id) REFERENCES declaration (id)
);
